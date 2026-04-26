"""Bounded Skills-style action loop for document modification proposals."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from pydantic import ValidationError

from . import llm_client, tools
from .config_loader import load_agentic_modification_skill_config
from .models import (
    AgentAction,
    AgenticModificationSkillRunResult,
    DelegateFileEditsArguments,
    FetchChunkWindowArguments,
    FetchFileOutlineArguments,
    FetchParentChunkArguments,
    FinishArguments,
    LoadSkillArguments,
    ProposalItem,
    ReadReferenceArguments,
    SearchContextArguments,
    SearchFilesArguments,
)

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]

_DEFAULT_MAX_STEPS = 12
_MAX_STEPS_CAP = 16
_DEFAULT_TIMEOUT_SECONDS = 240.0
_MAX_TOOL_SUMMARY_CHARS = 1400


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _safe_json_object(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start == -1:
            raise
        payload, _end_index = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(payload, dict):
        raise ValueError("Model returned a non-object action payload.")
    return payload


def _trim(raw: Any, *, max_chars: int = _MAX_TOOL_SUMMARY_CHARS) -> str:
    text = str(raw or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def _normalize_proposals(raw: list[ProposalItem] | list[dict[str, Any]]) -> list[ProposalItem]:
    proposals: list[ProposalItem] = []
    seen_parent_ids: set[str] = set()
    for raw_item in raw:
        item = raw_item if isinstance(raw_item, ProposalItem) else ProposalItem.model_validate(raw_item)
        if item.parentId in seen_parent_ids:
            continue
        if item.original.strip() == item.proposed.strip():
            continue
        seen_parent_ids.add(item.parentId)
        proposals.append(item)
    return proposals


class CoverageLedger:
    def __init__(self) -> None:
        self.searched_queries: list[str] = []
        self.discovered_candidate_files: dict[str, str] = {}
        self.explored_files: set[str] = set()
        self.delegated_files: set[str] = set()
        self.explored_parent_chunks: set[str] = set()
        self.edited_parent_chunks: set[str] = set()
        self.skipped_candidates: dict[str, str] = {}

    def observe_file(self, file_id: str, file_name: str | None = None) -> None:
        normalized = str(file_id or "").strip()
        if not normalized:
            return
        self.discovered_candidate_files.setdefault(normalized, str(file_name or "unknown").strip() or "unknown")

    def observe_search(self, query: str) -> None:
        normalized = str(query or "").strip()
        if normalized:
            self.searched_queries.append(normalized)

    def report(self) -> dict[str, Any]:
        uncovered = [
            {"file_id": file_id, "file_name": file_name}
            for file_id, file_name in sorted(self.discovered_candidate_files.items())
            if file_id not in self.delegated_files
            and file_id not in self.skipped_candidates
        ]
        return {
            "searched_queries": self.searched_queries,
            "discovered_candidate_files": [
                {"file_id": file_id, "file_name": file_name}
                for file_id, file_name in sorted(self.discovered_candidate_files.items())
            ],
            "explored_files": sorted(self.explored_files),
            "delegated_files": sorted(self.delegated_files),
            "explored_parent_chunks": sorted(self.explored_parent_chunks),
            "edited_parent_chunks": sorted(self.edited_parent_chunks),
            "skipped_candidates": [
                {"file_id": file_id, "reason": reason}
                for file_id, reason in sorted(self.skipped_candidates.items())
            ],
            "uncovered_candidate_files": uncovered,
        }

    def uncovered_file_ids(self) -> list[str]:
        return [
            item["file_id"]
            for item in self.report().get("uncovered_candidate_files", [])
            if isinstance(item, dict) and item.get("file_id")
        ]


def _build_registry_message(config: Any) -> str:
    return _json_dumps(
        {
            "skill_registry": [
                metadata.as_registry_item()
                for metadata in sorted(config.skill_registry.values(), key=lambda item: item.name)
            ],
            "action_protocol": {
                "shape": {
                    "action": "load_skill|search_files|search_context|fetch_file_outline|fetch_parent_chunk|fetch_chunk_window|delegate_file_edits|read_reference|finish",
                    "arguments": {},
                    "intent": "short operational sentence",
                    "success_criteria": "short sufficiency condition",
                    "fallback": "short fallback if insufficient",
                },
                "examples": {
                    "load_skill": {"skill_name": "document-modification"},
                    "search_context": {"query": "refund policy 14 days", "top_k": 8},
                    "delegate_file_edits": {"file_ids": ["file-1"], "instruction": "Change refund period to 30 days."},
                    "finish": {"proposals": [], "skipped_candidates": []},
                },
            },
        }
    )


def _build_state_update(
    *,
    step: int,
    max_steps: int,
    timeout_s: float,
    ledger: CoverageLedger,
    proposal_count: int,
) -> str:
    return (
        f"Runtime step {step}/{max_steps}. Timeout seconds: {timeout_s}.\n"
        f"Current proposal count: {proposal_count}.\n"
        f"Coverage ledger:\n{_json_dumps(ledger.report())}\n\n"
        "Return only the next JSON action-state object."
    )


async def _emit_progress(
    progress_callback: ProgressCallback | None,
    *,
    stage: str,
    status_value: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if progress_callback is None:
        return
    payload: dict[str, Any] = {
        "stage": stage,
        "status": status_value,
        "message": message,
        "timestamp": _now_iso(),
    }
    if metadata:
        payload["metadata"] = metadata
    await progress_callback(payload)


def _tool_observation(action_name: str, payload: Any) -> str:
    if action_name == "search_files":
        return f"search_files returned {len(payload or [])} file match(es)."
    if action_name == "search_context":
        return f"search_context returned {len(payload or [])} evidence item(s)."
    if action_name == "fetch_file_outline":
        return f"fetch_file_outline returned {len(payload or [])} outline chunk(s)."
    if action_name == "fetch_parent_chunk":
        return "fetch_parent_chunk returned a parent chunk." if payload else "fetch_parent_chunk returned no scoped chunk."
    if action_name == "fetch_chunk_window":
        chunks = getattr(payload, "chunks", []) if payload is not None else []
        return f"fetch_chunk_window returned {len(chunks)} chunk(s)."
    if action_name == "delegate_file_edits":
        proposal_count = sum(len(item.proposals) for item in payload or [])
        return f"delegate_file_edits returned {proposal_count} proposal(s) across {len(payload or [])} file worker result(s)."
    if action_name == "load_skill":
        return f"load_skill loaded {payload.get('skill_name') if isinstance(payload, dict) else 'skill'}."
    if action_name == "read_reference":
        return f"read_reference returned {len(str(payload or ''))} character(s)."
    return _trim(payload)


def _apply_worker_results(
    *,
    worker_results: list[Any],
    ledger: CoverageLedger,
    existing_proposals: list[ProposalItem],
) -> list[ProposalItem]:
    for result in worker_results:
        ledger.observe_file(result.file_id, result.file_name)
        ledger.delegated_files.add(result.file_id)
        if result.explored_parent_ids:
            ledger.explored_files.add(result.file_id)
        for parent_id in result.explored_parent_ids:
            ledger.explored_parent_chunks.add(parent_id)
        if result.skipped and result.reason:
            ledger.skipped_candidates[result.file_id] = result.reason
        for proposal in result.proposals:
            ledger.skipped_candidates.pop(result.file_id, None)
            ledger.edited_parent_chunks.add(proposal.parentId)
    return _normalize_proposals(
        [*existing_proposals, *[proposal for result in worker_results for proposal in result.proposals]]
    )


async def _run_loop(
    *,
    user_instruction: str,
    user_id: str,
    included_file_ids: list[str] | None,
    max_steps: int,
    timeout_s: float,
    run_id: str,
    session: Any,
    progress_callback: ProgressCallback | None,
) -> AgenticModificationSkillRunResult:
    config = load_agentic_modification_skill_config()
    parent_doc_cache: dict[str, dict[str, Any]] = {}
    ledger = CoverageLedger()
    proposals: list[ProposalItem] = []
    tool_call_count = 0
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    llm_call_count = 0

    # Build the initial system and user messages
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": config.system_prompt},
        {"role": "system", "content": _build_registry_message(config)},
        {
            "role": "user",
            "content": _json_dumps(
                {
                    "user_instruction": user_instruction,
                    "included_file_ids": included_file_ids,
                    "run_id": run_id,
                }
            ),
        },
    ]

    try:
        # Initial seeding of candidate files based on user instruction
        seeded_matches = await tools.search_files_tool(
            query=user_instruction,
            limit=10,
            user_id=user_id,
            included_file_ids=included_file_ids,
        )
        if seeded_matches:
            tool_call_count += 1
            ledger.observe_search(user_instruction)
            for match in seeded_matches:
                ledger.observe_file(match.file_id, match.file_name)
            messages.append(
                {
                    "role": "user",
                    "content": "Initial candidate file discovery: "
                    + _trim(
                        _json_dumps(
                            {
                                "query": user_instruction,
                                "matches": [match.model_dump() for match in seeded_matches],
                                "coverage_report": ledger.report(),
                            }
                        )
                    ),
                }
            )
    except Exception as error:
        messages.append(
            {
                "role": "user",
                "content": f"Initial candidate file discovery failed: {error}",
            }
        )

    for step in range(1, max_steps + 1):
        messages.append(
            {
                "role": "system",
                "content": _build_state_update(
                    step=step,
                    max_steps=max_steps,
                    timeout_s=timeout_s,
                    ledger=ledger,
                    proposal_count=len(proposals),
                ),
            }
        )
        llm_text, usage = await llm_client.call_action_model(
            messages=messages,
            session=session,
            max_tokens=1400,
            timeout_s=min(120.0, max(10.0, timeout_s)),
        )
        llm_call_count += 1
        for key in usage_total:
            usage_total[key] += int(usage.get(key, 0) or 0)

        try:
            action = AgentAction.model_validate(_safe_json_object(llm_text))
        except (ValueError, ValidationError, json.JSONDecodeError) as error:
            observation = f"Invalid action payload: {error}"
            messages.append({"role": "assistant", "content": llm_text})
            messages.append({"role": "user", "content": f"Tool result: {observation}"})
            continue

        messages.append({"role": "assistant", "content": _json_dumps(action.model_dump())})
        await _emit_progress(
            progress_callback,
            stage="agentic_modification_skill_step",
            status_value="started",
            message=f"Step {step}: executing {action.action}",
            metadata={"action": action.action, "intent": action.intent},
        )

        step_error: str | None = None
        tool_payload: Any = None
        try:
            if action.action == "load_skill":
                args = LoadSkillArguments.model_validate(action.arguments)
                tool_payload = tools.load_skill_tool(skill_name=args.skill_name, config=config)
                tool_call_count += 1

            elif action.action == "read_reference":
                args = ReadReferenceArguments.model_validate(action.arguments)
                tool_payload = tools.read_reference_tool(
                    skill_name=args.skill_name,
                    ref_id=args.ref_id,
                    config=config,
                )
                tool_call_count += 1

            elif action.action == "search_files":
                args = SearchFilesArguments.model_validate(action.arguments)
                matches = await tools.search_files_tool(
                    query=args.query,
                    limit=args.limit,
                    user_id=user_id,
                    included_file_ids=included_file_ids,
                )
                ledger.observe_search(args.query)
                for match in matches:
                    ledger.observe_file(match.file_id, match.file_name)
                tool_payload = matches
                tool_call_count += 1

            elif action.action == "search_context":
                args = SearchContextArguments.model_validate(action.arguments)
                evidence = await tools.search_context_tool(
                    query=args.query,
                    top_k=args.top_k,
                    user_id=user_id,
                    included_file_ids=included_file_ids,
                    parent_doc_cache=parent_doc_cache,
                )
                ledger.observe_search(args.query)
                for item in evidence:
                    ledger.observe_file(item.file_id, item.file_name)
                    ledger.explored_parent_chunks.add(item.parent_id)
                tool_payload = evidence
                tool_call_count += 1

            elif action.action == "fetch_file_outline":
                args = FetchFileOutlineArguments.model_validate(action.arguments)
                outline = await tools.fetch_file_outline_tool(
                    file_id=args.file_id,
                    file_name=args.file_name,
                    max_chunks=args.max_chunks,
                    user_id=user_id,
                    included_file_ids=included_file_ids,
                    parent_doc_cache=parent_doc_cache,
                )
                for item in outline:
                    ledger.observe_file(item.file_id, item.file_name)
                    ledger.explored_files.add(item.file_id)
                tool_payload = outline
                tool_call_count += 1

            elif action.action == "fetch_parent_chunk":
                args = FetchParentChunkArguments.model_validate(action.arguments)
                chunk = await tools.fetch_parent_chunk_tool(
                    parent_id=args.parent_id,
                    user_id=user_id,
                    included_file_ids=included_file_ids,
                    parent_doc_cache=parent_doc_cache,
                )
                if chunk is not None:
                    ledger.observe_file(chunk.file_id, chunk.file_name)
                    ledger.explored_files.add(chunk.file_id)
                    ledger.explored_parent_chunks.add(chunk.parent_id)
                tool_payload = chunk
                tool_call_count += 1

            elif action.action == "fetch_chunk_window":
                args = FetchChunkWindowArguments.model_validate(action.arguments)
                window = await tools.fetch_chunk_window_tool(
                    file_id=args.file_id,
                    center_parent_id=args.center_parent_id,
                    center_chunk_number=args.center_chunk_number,
                    before=args.before,
                    after=args.after,
                    user_id=user_id,
                    included_file_ids=included_file_ids,
                    parent_doc_cache=parent_doc_cache,
                )
                for chunk in window.chunks:
                    ledger.observe_file(chunk.file_id, chunk.file_name)
                    ledger.explored_files.add(chunk.file_id)
                    ledger.explored_parent_chunks.add(chunk.parent_id)
                tool_payload = window
                tool_call_count += 1

            elif action.action == "delegate_file_edits":
                args = DelegateFileEditsArguments.model_validate(action.arguments)
                instruction = str(args.instruction or user_instruction).strip()
                worker_results, worker_usage, worker_calls = await tools.delegate_file_edits_tool(
                    file_ids=args.file_ids,
                    instruction=instruction,
                    user_id=user_id,
                    included_file_ids=included_file_ids,
                    parent_doc_cache=parent_doc_cache,
                    session=session,
                    timeout_s=timeout_s,
                )
                llm_call_count += worker_calls
                for key in usage_total:
                    usage_total[key] += int(worker_usage.get(key, 0) or 0)
                proposals = _apply_worker_results(
                    worker_results=worker_results,
                    ledger=ledger,
                    existing_proposals=proposals,
                )
                tool_payload = worker_results
                tool_call_count += 1

            elif action.action == "finish":
                args = FinishArguments.model_validate(action.arguments)
                for skipped in args.skipped_candidates:
                    if skipped.file_id:
                        ledger.skipped_candidates[skipped.file_id] = skipped.reason
                proposals = _normalize_proposals([*proposals, *args.proposals])
                for proposal in proposals:
                    ledger.observe_file(proposal.fileId, proposal.fileName)
                    ledger.edited_parent_chunks.add(proposal.parentId)
                    ledger.delegated_files.add(proposal.fileId)
                if (
                    not proposals
                    and not ledger.discovered_candidate_files
                    and not ledger.skipped_candidates
                    and step < max_steps
                ):
                    tool_payload = {
                        "rejected": True,
                        "reason": (
                            "Cannot finish yet. No candidate files have been discovered. "
                            "Search files or document context before concluding there are no changes."
                        ),
                        "coverage_report": ledger.report(),
                    }
                    messages.append({"role": "user", "content": f"Tool result: {_json_dumps(tool_payload)}"})
                    await _emit_progress(
                        progress_callback,
                        stage="agentic_modification_skill_step",
                        status_value="completed",
                        message="Finish rejected because no candidate files were discovered.",
                        metadata={"action": action.action},
                    )
                    continue
                uncovered = ledger.uncovered_file_ids()
                if uncovered:
                    tool_payload = {
                        "rejected": True,
                        "reason": f"Cannot finish yet. Uncovered candidate files: {', '.join(uncovered)}",
                        "coverage_report": ledger.report(),
                    }
                    messages.append({"role": "user", "content": f"Tool result: {_json_dumps(tool_payload)}"})
                    await _emit_progress(
                        progress_callback,
                        stage="agentic_modification_skill_step",
                        status_value="completed",
                        message="Finish rejected because candidate coverage is incomplete.",
                        metadata={"action": action.action, "uncoveredFileIds": uncovered},
                    )
                    continue
                return AgenticModificationSkillRunResult(
                    proposals=proposals,
                    goal=user_instruction,
                    lexical_anchors=[],
                    semantic_anchors=[],
                    anchors=[],
                    constraint="None",
                    skill_runtime_result={
                        "summary": args.summary or "",
                        "steps": step,
                    },
                    coverage_report=ledger.report(),
                    run_id=run_id,
                    termination_reason="finished",
                    tool_call_count=tool_call_count,
                    token_prompt_total=usage_total["prompt_tokens"],
                    token_completion_total=usage_total["completion_tokens"],
                    token_total=usage_total["total_tokens"],
                    llm_call_count=llm_call_count,
                )

        except Exception as error:
            step_error = str(error)
            tool_payload = {"error": step_error}

        observation = _tool_observation(action.action, tool_payload)
        if step_error:
            observation = f"{observation} Error: {step_error}"
        messages.append(
            {
                "role": "user",
                "content": "Tool result: "
                + _trim(
                    _json_dumps(
                        {
                            "observation": observation,
                            "payload": (
                                [item.model_dump() for item in tool_payload]
                                if isinstance(tool_payload, list) and all(hasattr(item, "model_dump") for item in tool_payload)
                                else tool_payload.model_dump()
                                if hasattr(tool_payload, "model_dump")
                                else tool_payload
                            ),
                            "coverage_report": ledger.report(),
                        }
                    )
                ),
            }
        )
        await _emit_progress(
            progress_callback,
            stage="agentic_modification_skill_step",
            status_value="failed" if step_error else "completed",
            message=f"Step {step}: {'failed' if step_error else 'completed'} {action.action}",
            metadata={"action": action.action, "observation": observation},
        )

    forced_delegate_file_ids = ledger.uncovered_file_ids()
    if forced_delegate_file_ids:
        await _emit_progress(
            progress_callback,
            stage="agentic_modification_skill_step",
            status_value="started",
            message="Step limit reached: delegating remaining candidate files for edits.",
            metadata={"action": "forced_delegate_file_edits", "fileIds": forced_delegate_file_ids},
        )
        try:
            worker_results, worker_usage, worker_calls = await tools.delegate_file_edits_tool(
                file_ids=forced_delegate_file_ids,
                instruction=user_instruction,
                user_id=user_id,
                included_file_ids=included_file_ids,
                parent_doc_cache=parent_doc_cache,
                session=session,
                timeout_s=timeout_s,
            )
            tool_call_count += 1
            llm_call_count += worker_calls
            for key in usage_total:
                usage_total[key] += int(worker_usage.get(key, 0) or 0)
            proposals = _apply_worker_results(
                worker_results=worker_results,
                ledger=ledger,
                existing_proposals=proposals,
            )
            await _emit_progress(
                progress_callback,
                stage="agentic_modification_skill_step",
                status_value="completed",
                message="Remaining candidate files were delegated for edits.",
                metadata={
                    "action": "forced_delegate_file_edits",
                    "observation": _tool_observation("delegate_file_edits", worker_results),
                },
            )
        except Exception as error:
            await _emit_progress(
                progress_callback,
                stage="agentic_modification_skill_step",
                status_value="failed",
                message="Forced delegation failed.",
                metadata={"action": "forced_delegate_file_edits", "error": str(error)},
            )

    return AgenticModificationSkillRunResult(
        proposals=proposals,
        goal=user_instruction,
        skill_runtime_result={"summary": "Returned proposals after max steps and forced delegation.", "steps": max_steps},
        coverage_report=ledger.report(),
        run_id=run_id,
        termination_reason="forced_delegate_after_max_steps" if forced_delegate_file_ids else "max_steps_exceeded",
        tool_call_count=tool_call_count,
        token_prompt_total=usage_total["prompt_tokens"],
        token_completion_total=usage_total["completion_tokens"],
        token_total=usage_total["total_tokens"],
        llm_call_count=llm_call_count,
    )


async def run_agentic_modification_skill(
    *,
    user_instruction: str,
    user_id: str,
    included_file_ids: list[str] | None = None,
    max_steps: int = _DEFAULT_MAX_STEPS,
    timeout_s: float = _DEFAULT_TIMEOUT_SECONDS,
    progress_callback: ProgressCallback | None = None,
) -> AgenticModificationSkillRunResult:
    normalized_instruction = str(user_instruction or "").strip()
    if not normalized_instruction:
        raise ValueError("user_instruction must not be empty.")
    run_id = uuid4().hex
    normalized_max_steps = max(1, min(_MAX_STEPS_CAP, int(max_steps)))
    normalized_timeout = max(10.0, float(timeout_s or _DEFAULT_TIMEOUT_SECONDS))

    await _emit_progress(
        progress_callback,
        stage="agentic_modification_skill_pipeline",
        status_value="started",
        message="Skills modification agent started.",
        metadata={"runId": run_id},
    )

    try:
        result = await asyncio.wait_for(
            _run_loop(
                user_instruction=normalized_instruction,
                user_id=user_id,
                included_file_ids=included_file_ids,
                max_steps=normalized_max_steps,
                timeout_s=normalized_timeout,
                run_id=run_id,
                session=None,
                progress_callback=progress_callback,
            ),
            timeout=normalized_timeout,
        )
    except asyncio.TimeoutError:
        result = AgenticModificationSkillRunResult(
            proposals=[],
            goal=normalized_instruction,
            skill_runtime_result={"summary": "Skills modification agent timed out."},
            coverage_report={},
            run_id=run_id,
            termination_reason="timeout",
        )

    await _emit_progress(
        progress_callback,
        stage="agentic_modification_skill_pipeline",
        status_value="completed" if result.termination_reason != "timeout" else "failed",
        message="Skills modification agent completed." if result.termination_reason != "timeout" else "Skills modification agent timed out.",
        metadata={"runId": run_id, "proposalCount": len(result.proposals)},
    )
    return result
