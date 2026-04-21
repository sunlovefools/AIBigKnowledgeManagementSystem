"""Agentic query runtime using a strict JSON action protocol.

Architecture in one sentence:
`skills.md` defines behavior policy, while this runtime enforces execution,
validation, scoping, bounded loops, and deterministic termination.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Awaitable, Callable
from uuid import uuid4

from pydantic import ValidationError

from . import llm_client, tools
from .config_loader import load_agentic_query_config
from .models import (
    AgentAction,
    AgenticQueryRunResult,
    FetchParentChunkArguments,
    FinishArguments,
    ReadReferenceArguments,
    SearchContextArguments,
)

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]

# Optional debug logger import.
# The runtime is intentionally resilient: if debug logger is unavailable,
# agentic query still works with no-op logging functions.
try:
    from backend.debug.debug_logger import (
        log_agentic_query_action,
        log_agentic_query_config_event,
        log_agentic_query_llm_request,
        log_agentic_query_llm_response,
    )
except Exception:
    try:
        from debug.debug_logger import (  # type: ignore
            log_agentic_query_action,
            log_agentic_query_config_event,
            log_agentic_query_llm_request,
            log_agentic_query_llm_response,
        )
    except Exception:
        def log_agentic_query_action(**_kwargs):
            return None

        def log_agentic_query_config_event(**_kwargs):
            return None

        def log_agentic_query_llm_request(**_kwargs):
            return None

        def log_agentic_query_llm_response(**_kwargs):
            return None

_DEFAULT_TIMEOUT_SECONDS = 60.0
_DEFAULT_MAX_STEPS = 6
_DEFAULT_SEED_TOP_K = 8
_MAX_STEPS_CAP = 12
_MIN_STEPS_CAP = 1
_MAX_TOOL_SUMMARY_CHARS = 1200
_MAX_OBSERVATIONS = 8
_MAX_TRACE_TEXT_CHARS = 240
_MAX_TRACE_ARGUMENTS_CHARS = 320
_MAX_TRACE_ROWS = 6

# Supports models that accidentally wrap JSON in markdown fences.
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", re.IGNORECASE)


def _safe_json_object(raw_text: str) -> dict[str, Any]:
    """Parse the first valid JSON object from raw model text.

    Accepted forms:
    - pure JSON object
    - fenced code block containing a JSON object
    - text containing one JSON object substring
    """

    stripped = str(raw_text or "").strip()
    if not stripped:
        raise ValueError("Model returned empty action payload.")

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = _JSON_BLOCK_PATTERN.search(stripped)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    object_match = re.search(r"(\{[\s\S]*\})", stripped)
    if object_match:
        parsed = json.loads(object_match.group(1))
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Model output did not contain a valid JSON object.")


def _normalize_citations(
    raw_citations: list[str],
    *,
    allowed_file_names: set[str],
) -> list[str]:
    """Normalize and filter citations against observed in-scope file names."""

    allowed_case_map = {name.lower(): name for name in allowed_file_names}
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_citations:
        key = str(item or "").strip().lower()
        if not key or key in seen:
            continue
        allowed_name = allowed_case_map.get(key)
        if not allowed_name:
            continue
        seen.add(key)
        normalized.append(allowed_name)
    return normalized


def _summarize_evidence_cache(parent_doc_cache: dict[str, dict[str, Any]]) -> str:
    """Render a compact evidence summary included in each model turn."""

    lines: list[str] = []
    for index, (parent_id, doc) in enumerate(parent_doc_cache.items(), start=1):
        if index > 8:
            lines.append("- ...")
            break
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        file_metadata = (
            metadata.get("file_metadata")
            if isinstance(metadata.get("file_metadata"), dict)
            else {}
        )
        file_name = str(file_metadata.get("file_name") or metadata.get("source") or "unknown").strip() or "unknown"
        parent_chunk_metadata = (
            metadata.get("parent_chunk_metadata")
            if isinstance(metadata.get("parent_chunk_metadata"), dict)
            else {}
        )
        chunk_number = parent_chunk_metadata.get("parent_chunk_number")
        chunk_label = str(chunk_number) if isinstance(chunk_number, (int, float, str)) else "?"
        cached_snippet = str(doc.get("_agentic_query_snippet") or "").strip()
        snippet = cached_snippet or " ".join(str(doc.get("page_content") or "").split())[:1200]
        lines.append(
            f"- parent_id={parent_id}, file={file_name}, chunk={chunk_label}, snippet={snippet}"
        )
    return "\n".join(lines) if lines else "- (no evidence cached yet)"


def _trim_observation(raw: str, *, max_chars: int = _MAX_TOOL_SUMMARY_CHARS) -> str:
    """Collapse whitespace and bound observation size for prompt stability."""

    compact = " ".join(str(raw or "").split())
    return compact[:max_chars]


def _trace_text(raw: Any, *, max_chars: int = _MAX_TRACE_TEXT_CHARS) -> str | None:
    """Normalize optional model trace text into bounded single-line strings."""

    text = " ".join(str(raw or "").split())
    if not text:
        return None
    return text[:max_chars]


def _arguments_preview(arguments: dict[str, Any], *, max_chars: int = _MAX_TRACE_ARGUMENTS_CHARS) -> str:
    """Create a compact JSON-ish argument preview for prompts and progress UI."""

    if not isinstance(arguments, dict) or not arguments:
        return "{}"
    try:
        rendered = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        rendered = str(arguments)
    return _trim_observation(rendered, max_chars=max_chars)


def _summarize_step_trace_rows(step_traces: list[dict[str, Any]]) -> str:
    """Render recent structured step traces that the model can continue from."""

    if not step_traces:
        return "- (none yet)"

    rows: list[str] = []
    for trace in step_traces[-_MAX_TRACE_ROWS:]:
        step = trace.get("step")
        action = str(trace.get("action") or "").strip() or "unknown"
        arguments_preview = str(trace.get("arguments_preview") or "{}")
        observation = str(trace.get("observation") or "").strip() or "(no observation)"
        intent = str(trace.get("intent") or "").strip()
        decision = str(trace.get("decision") or "").strip()

        row = f"- Step {step}: action={action}, args={arguments_preview}, observation={observation}"
        if intent:
            row += f", intent={intent}"
        if decision:
            row += f", decision={decision}"
        rows.append(_trim_observation(row))
    return "\n".join(rows)


async def _emit_progress(
    callback: ProgressCallback | None,
    *,
    stage: str,
    status: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Best-effort progress callback emission for SSE/status streaming."""

    if callback is None:
        return
    event: dict[str, Any] = {
        "stage": stage,
        "status": status,
        "message": message,
    }
    if isinstance(metadata, dict) and metadata:
        event["metadata"] = metadata
    try:
        await callback(event)
    except Exception:
        return


async def _run_loop(
    *,
    user_query: str,
    user_id: str,
    included_file_ids: list[str],
    seed_top_k: int,
    max_steps: int,
    run_id: str,
    timeout_s: float,
    progress_callback: ProgressCallback | None,
) -> AgenticQueryRunResult:
    """Execute the bounded agentic action loop for one query run.

    Stop conditions handled here:
    - `finish` action returned by model
    - forced final `finish` pass after max steps
    - fallback after max steps when forced pass fails
    """
    config = load_agentic_query_config()
    cache_info = load_agentic_query_config.cache_info()
    parent_doc_cache: dict[str, dict[str, Any]] = {}
    observed_file_names: set[str] = set()
    used_reference_ids: set[str] = set()
    observations: list[str] = []
    step_traces: list[dict[str, Any]] = []
    tool_call_count = 0

    # Snapshot resolved markdown/runtime config once per run for observability.
    log_agentic_query_config_event(
        run_id=run_id,
        event="run_config_snapshot",
        payload={
            "skills_md_path": str(config.skills_path),
            "skills_md_loaded": True,
            "skills_prompt_length": len(config.system_prompt),
            "reference_root": str(config.reference_root),
            "available_reference_ids": sorted(config.reference_paths.keys()),
            "references_injected_by_default": False,
            "included_file_ids_count": len(included_file_ids),
            "config_cache_info": {
                "hits": int(cache_info.hits),
                "misses": int(cache_info.misses),
                "currsize": int(cache_info.currsize),
                "maxsize": int(cache_info.maxsize or 0),
            },
        },
    )

    await _emit_progress(
        progress_callback,
        stage="agentic_query_pipeline",
        status="started",
        message="Agentic query run started.",
        metadata={"runId": run_id},
    )

    # Seed retrieval keeps first model turn grounded without inflating prompt context.
    seed_evidence = await tools.search_context_tool(
        query=user_query,
        top_k=seed_top_k,
        user_id=user_id,
        included_file_ids=included_file_ids,
        parent_doc_cache=parent_doc_cache,
    )
    tool_call_count += 1
    for item in seed_evidence:
        observed_file_names.add(item.file_name)
    log_agentic_query_action(
        run_id=run_id,
        step=0,
        action="seed_search_context",
        arguments={
            "query": user_query,
            "top_k": int(seed_top_k),
            "included_file_ids_count": len(included_file_ids),
        },
        result={
            "evidence_count": len(seed_evidence),
            "parent_ids": [item.parent_id for item in seed_evidence[:12]],
            "file_names": sorted({item.file_name for item in seed_evidence}),
            "snippets": [
                {
                    "parent_id": item.parent_id,
                    "file_name": item.file_name,
                    "snippet": item.snippet,
                }
                for item in seed_evidence[:5]
            ],
        },
    )

    await _emit_progress(
        progress_callback,
        stage="agentic_query_seed",
        status="completed",
        message="Initial retrieval seed completed.",
        metadata={"runId": run_id, "seedCount": len(seed_evidence)},
    )
    if seed_evidence:
        observations.append(
            _trim_observation(
                "Seed retrieval found parent_ids: "
                + ", ".join(item.parent_id for item in seed_evidence[:8])
            )
        )

    # Main bounded loop: each turn asks the model for exactly one tool action.
    for step in range(1, max_steps + 1):
        evidence_summary = _summarize_evidence_cache(parent_doc_cache)
        recent_observations = "\n".join(f"- {item}" for item in observations[-_MAX_OBSERVATIONS:])
        if not recent_observations:
            recent_observations = "- (none)"
        recent_structured_trace = _summarize_step_trace_rows(step_traces)

        user_prompt = (
            f"User Query:\n{user_query}\n\n"
            f"Step: {step}/{max_steps}\n"
            f"Timeout (seconds): {timeout_s}\n\n"
            "Current Evidence Cache:\n"
            f"{evidence_summary}\n\n"
            "Recent Tool Observations:\n"
            f"{recent_observations}\n\n"
            "Recent Structured Step Trace:\n"
            f"{recent_structured_trace}\n\n"
            "Return ONLY one JSON object with this schema:\n"
            '{"action":"search_context|fetch_parent_chunk|read_reference|finish","arguments":{...},"intent":"optional short string","decision":"optional short string"}\n'
            "Action argument schema:\n"
            '- search_context: {"query":"string","top_k":int}\n'
            '- fetch_parent_chunk: {"parent_id":"string"}\n'
            '- read_reference: {"ref_id":"string"}\n'
            '- finish: {"answer":"string","citations":["file_name"]}\n'
            "Trace guidance:\n"
            "- intent: what you are trying this step.\n"
            "- decision: one-line summary of what to do next if this step is insufficient.\n"
            "\nDecision rules:\n"
            "- If the current evidence cache contains a direct answer, return finish.\n"
            "- Do not repeat the same search_context query after it has already returned evidence.\n"
            "- On the final step, return finish using the best available evidence.\n"
        )
        log_agentic_query_llm_request(
            run_id=run_id,
            step=step,
            system_prompt=config.system_prompt,
            user_prompt=user_prompt,
        )

        # Runtime always calls with the markdown policy as system prompt.
        llm_response_text, _usage = await llm_client.call_action_model(
            messages=[
                {"role": "system", "content": config.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=512,
            timeout_s=min(120.0, max(10.0, timeout_s)),
        )
        log_agentic_query_llm_response(
            run_id=run_id,
            step=step,
            response_text=llm_response_text,
        )

        # Parse and schema-validate model response into one action.
        try:
            action_payload = _safe_json_object(llm_response_text)
            action = AgentAction.model_validate(action_payload)
        except (ValueError, ValidationError) as error:
            invalid_observation = _trim_observation(f"Invalid action payload: {error}")
            observations.append(invalid_observation)
            step_traces.append(
                {
                    "step": step,
                    "action": "invalid_action_payload",
                    "arguments_preview": "{}",
                    "intent": "",
                    "observation": invalid_observation,
                    "decision": "",
                }
            )
            log_agentic_query_action(
                run_id=run_id,
                step=step,
                action="invalid_action_payload",
                arguments={},
                result={"raw_response_preview": str(llm_response_text)[:1200]},
                error=str(error),
            )
            await _emit_progress(
                progress_callback,
                stage="agentic_query_step",
                status="warning",
                message="Model returned invalid action payload.",
                metadata={
                    "runId": run_id,
                    "step": step,
                    "action": "invalid_action_payload",
                    "tool": "invalid_action_payload",
                    "observation": invalid_observation,
                },
            )
            continue

        trace_intent = _trace_text(action.intent)
        trace_decision = _trace_text(action.decision)
        arguments_preview = _arguments_preview(dict(action.arguments or {}))
        step_observation = ""
        step_error: str | None = None

        await _emit_progress(
            progress_callback,
            stage="agentic_query_step",
            status="started",
            message=f"Step {step}: executing {action.action}",
            metadata={
                "runId": run_id,
                "step": step,
                "action": action.action,
                "tool": action.action,
                "intent": trace_intent,
                "decision": trace_decision,
                "argumentsPreview": arguments_preview,
            },
        )

        # Tool dispatch block: each action validates typed arguments before execution.
        try:
            if action.action == "search_context":
                args = SearchContextArguments.model_validate(action.arguments)
                evidence = await tools.search_context_tool(
                    query=args.query,
                    top_k=args.top_k,
                    user_id=user_id,
                    included_file_ids=included_file_ids,
                    parent_doc_cache=parent_doc_cache,
                )
                tool_call_count += 1
                for item in evidence:
                    observed_file_names.add(item.file_name)
                step_observation = _trim_observation(
                    "search_context returned "
                    f"{len(evidence)} items for query={args.query!r} top_k={int(args.top_k)}."
                )
                log_agentic_query_action(
                    run_id=run_id,
                    step=step,
                    action="search_context",
                    arguments={
                        "query": args.query,
                        "top_k": int(args.top_k),
                        "intent": trace_intent,
                        "decision": trace_decision,
                    },
                    result={
                        "evidence_count": len(evidence),
                        "parent_ids": [item.parent_id for item in evidence[:12]],
                        "file_names": sorted({item.file_name for item in evidence}),
                        "snippets": [
                            {
                                "parent_id": item.parent_id,
                                "file_name": item.file_name,
                                "snippet": item.snippet,
                            }
                            for item in evidence[:5]
                        ],
                    },
                )
            elif action.action == "fetch_parent_chunk":
                args = FetchParentChunkArguments.model_validate(action.arguments)
                item = await tools.fetch_parent_chunk_tool(
                    parent_id=args.parent_id,
                    user_id=user_id,
                    included_file_ids=included_file_ids,
                    parent_doc_cache=parent_doc_cache,
                )
                tool_call_count += 1
                if item is None:
                    step_observation = "fetch_parent_chunk returned no scoped record."
                    log_agentic_query_action(
                        run_id=run_id,
                        step=step,
                        action="fetch_parent_chunk",
                        arguments={
                            "parent_id": args.parent_id,
                            "intent": trace_intent,
                            "decision": trace_decision,
                        },
                        result={"found": False},
                    )
                else:
                    observed_file_names.add(item.file_name)
                    step_observation = _trim_observation(
                        "fetch_parent_chunk returned "
                        f"parent_id={item.parent_id} in file={item.file_name}."
                    )
                    log_agentic_query_action(
                        run_id=run_id,
                        step=step,
                        action="fetch_parent_chunk",
                        arguments={
                            "parent_id": args.parent_id,
                            "intent": trace_intent,
                            "decision": trace_decision,
                        },
                        result={
                            "found": True,
                            "file_name": item.file_name,
                            "file_id": item.file_id,
                            "parent_chunk_number": item.parent_chunk_number,
                        },
                    )
            elif action.action == "read_reference":
                args = ReadReferenceArguments.model_validate(action.arguments)
                ref_text = tools.read_reference_tool(
                    ref_id=args.ref_id,
                    config=config,
                    max_chars=1800,
                )
                tool_call_count += 1
                used_reference_ids.add(str(args.ref_id or "").strip().lower())
                step_observation = _trim_observation(
                    f"read_reference loaded ref_id={args.ref_id} with {len(ref_text)} chars."
                )
                log_agentic_query_action(
                    run_id=run_id,
                    step=step,
                    action="read_reference",
                    arguments={
                        "ref_id": args.ref_id,
                        "intent": trace_intent,
                        "decision": trace_decision,
                    },
                    result={
                        "content_length": len(ref_text),
                        "ref_path": str(
                            config.reference_paths.get(
                                str(args.ref_id or "").strip().lower()
                            )
                            or ""
                        ),
                    },
                )
            elif action.action == "finish":
                args = FinishArguments.model_validate(action.arguments)
                normalized_answer = str(args.answer or "").strip()
                if not normalized_answer:
                    normalized_answer = "No answer found in the provided context."
                normalized_citations = _normalize_citations(
                    args.citations,
                    allowed_file_names=observed_file_names,
                )
                # If model omits/invalidates citations, fallback to observed evidence files.
                if not normalized_citations:
                    normalized_citations = sorted(observed_file_names)
                step_observation = _trim_observation(
                    "finish returned final answer with "
                    f"{len(normalized_citations)} citations."
                )
                log_agentic_query_action(
                    run_id=run_id,
                    step=step,
                    action="finish",
                    arguments={
                        "citations_from_model": list(args.citations),
                        "intent": trace_intent,
                        "decision": trace_decision,
                    },
                    result={
                        "final_answer_length": len(normalized_answer),
                        "final_citations": list(normalized_citations),
                    },
                )

                step_traces.append(
                    {
                        "step": step,
                        "action": action.action,
                        "arguments_preview": arguments_preview,
                        "intent": trace_intent or "",
                        "observation": step_observation,
                        "decision": trace_decision or "",
                    }
                )
                observations.append(
                    _trim_observation(
                        f"Step {step} trace: action={action.action}, args={arguments_preview}, "
                        f"observation={step_observation}, intent={trace_intent or '(none)'}, "
                        f"decision={trace_decision or '(none)'}"
                    )
                )
                await _emit_progress(
                    progress_callback,
                    stage="agentic_query_step",
                    status="completed",
                    message=f"Step {step}: completed {action.action}",
                    metadata={
                        "runId": run_id,
                        "step": step,
                        "action": action.action,
                        "tool": action.action,
                        "intent": trace_intent,
                        "decision": trace_decision,
                        "argumentsPreview": arguments_preview,
                        "observation": step_observation,
                    },
                )

                await _emit_progress(
                    progress_callback,
                    stage="agentic_query_pipeline",
                    status="completed",
                    message="Agentic query run completed.",
                    metadata={
                        "runId": run_id,
                        "toolCallCount": tool_call_count,
                        "citationCount": len(normalized_citations),
                    },
                )
                log_agentic_query_config_event(
                    run_id=run_id,
                    event="run_completed_config_usage",
                    payload={
                        "skills_md_path": str(config.skills_path),
                        "skills_md_used_as_system_prompt": True,
                        "references_read_count": len(used_reference_ids),
                        "references_read_ids": sorted(used_reference_ids),
                        "references_not_read_ids": sorted(
                            set(config.reference_paths.keys()) - used_reference_ids
                        ),
                        "recent_step_trace": _summarize_step_trace_rows(step_traces),
                    },
                )
                return AgenticQueryRunResult(
                    answer=normalized_answer,
                    citations=normalized_citations,
                    run_id=run_id,
                    termination_reason="finished",
                    tool_call_count=tool_call_count,
                )
            else:
                step_observation = f"Unknown action received: {action.action}"
                log_agentic_query_action(
                    run_id=run_id,
                    step=step,
                    action="unknown_action",
                    arguments={
                        **dict(action.arguments or {}),
                        "intent": trace_intent,
                        "decision": trace_decision,
                    },
                    result={},
                    error=f"Unknown action: {action.action}",
                )
        except Exception as error:
            step_error = str(error)
            step_observation = _trim_observation(f"Action execution failed: {error}")
            log_agentic_query_action(
                run_id=run_id,
                step=step,
                action=action.action,
                arguments={
                    **dict(action.arguments or {}),
                    "intent": trace_intent,
                    "decision": trace_decision,
                },
                result={},
                error=str(error),
            )

        if not step_observation:
            step_observation = "Action completed."

        step_traces.append(
            {
                "step": step,
                "action": action.action,
                "arguments_preview": arguments_preview,
                "intent": trace_intent or "",
                "observation": step_observation,
                "decision": trace_decision or "",
            }
        )
        observations.append(
            _trim_observation(
                f"Step {step} trace: action={action.action}, args={arguments_preview}, "
                f"observation={step_observation}, intent={trace_intent or '(none)'}, "
                f"decision={trace_decision or '(none)'}"
            )
        )

        await _emit_progress(
            progress_callback,
            stage="agentic_query_step",
            status="failed" if step_error else "completed",
            message=f"Step {step}: completed {action.action}" if not step_error else f"Step {step}: failed {action.action}",
            metadata={
                "runId": run_id,
                "step": step,
                "action": action.action,
                "tool": action.action,
                "intent": trace_intent,
                "decision": trace_decision,
                "argumentsPreview": arguments_preview,
                "observation": step_observation,
                "error": step_error,
            },
        )

    # If loop ended without `finish`, force one last synthesis turn with no tools.
    if parent_doc_cache:
        forced_step = max_steps + 1
        evidence_summary = _summarize_evidence_cache(parent_doc_cache)
        forced_user_prompt = (
            f"User Query:\n{user_query}\n\n"
            "No more tool calls are allowed. Use only the evidence below and return finish.\n\n"
            "Current Evidence Cache:\n"
            f"{evidence_summary}\n\n"
            "Return ONLY this JSON object shape:\n"
            '{"action":"finish","arguments":{"answer":"string","citations":["file_name"]},"intent":"optional short string","decision":"optional short string"}\n'
            'If the evidence does not answer the query, use answer "No answer found in the provided context." and empty citations.\n'
        )
        await _emit_progress(
            progress_callback,
            stage="agentic_query_step",
            status="started",
            message=f"Step {forced_step}: forcing final finish",
            metadata={
                "runId": run_id,
                "step": forced_step,
                "action": "forced_finish",
                "tool": "forced_finish",
                "observation": "Max steps reached. No more tool calls allowed.",
            },
        )
        log_agentic_query_llm_request(
            run_id=run_id,
            step=forced_step,
            system_prompt=config.system_prompt,
            user_prompt=forced_user_prompt,
        )
        try:
            llm_response_text, _usage = await llm_client.call_action_model(
                messages=[
                    {"role": "system", "content": config.system_prompt},
                    {"role": "user", "content": forced_user_prompt},
                ],
                max_tokens=512,
                timeout_s=min(120.0, max(10.0, timeout_s)),
            )
            log_agentic_query_llm_response(
                run_id=run_id,
                step=forced_step,
                response_text=llm_response_text,
            )
            action_payload = _safe_json_object(llm_response_text)
            action = AgentAction.model_validate(action_payload)
            if action.action == "finish":
                args = FinishArguments.model_validate(action.arguments)
                normalized_answer = str(args.answer or "").strip()
                if not normalized_answer:
                    normalized_answer = "No answer found in the provided context."
                normalized_citations = _normalize_citations(
                    args.citations,
                    allowed_file_names=observed_file_names,
                )
                # Preserve empty citations only for explicit no-answer fallback text.
                if not normalized_citations and normalized_answer != "No answer found in the provided context.":
                    normalized_citations = sorted(observed_file_names)
                forced_intent = _trace_text(action.intent)
                forced_decision = _trace_text(action.decision)
                forced_observation = _trim_observation(
                    "forced_finish produced final answer with "
                    f"{len(normalized_citations)} citations."
                )
                log_agentic_query_action(
                    run_id=run_id,
                    step=forced_step,
                    action="forced_finish",
                    arguments={
                        "citations_from_model": list(args.citations),
                        "intent": forced_intent,
                        "decision": forced_decision,
                    },
                    result={
                        "final_answer_length": len(normalized_answer),
                        "final_citations": list(normalized_citations),
                    },
                )
                step_traces.append(
                    {
                        "step": forced_step,
                        "action": "forced_finish",
                        "arguments_preview": _arguments_preview(dict(action.arguments or {})),
                        "intent": forced_intent or "",
                        "observation": forced_observation,
                        "decision": forced_decision or "",
                    }
                )
                await _emit_progress(
                    progress_callback,
                    stage="agentic_query_step",
                    status="completed",
                    message=f"Step {forced_step}: completed forced finish",
                    metadata={
                        "runId": run_id,
                        "step": forced_step,
                        "action": "forced_finish",
                        "tool": "forced_finish",
                        "intent": forced_intent,
                        "decision": forced_decision,
                        "argumentsPreview": _arguments_preview(dict(action.arguments or {})),
                        "observation": forced_observation,
                    },
                )
                log_agentic_query_config_event(
                    run_id=run_id,
                    event="run_forced_finish_config_usage",
                    payload={
                        "skills_md_path": str(config.skills_path),
                        "skills_md_used_as_system_prompt": True,
                        "references_read_count": len(used_reference_ids),
                        "references_read_ids": sorted(used_reference_ids),
                        "references_not_read_ids": sorted(
                            set(config.reference_paths.keys()) - used_reference_ids
                        ),
                        "recent_step_trace": _summarize_step_trace_rows(step_traces),
                    },
                )
                return AgenticQueryRunResult(
                    answer=normalized_answer,
                    citations=normalized_citations,
                    run_id=run_id,
                    termination_reason="forced_finish_after_max_steps",
                    tool_call_count=tool_call_count,
                )
        except Exception as error:
            step_traces.append(
                {
                    "step": forced_step,
                    "action": "forced_finish_failed",
                    "arguments_preview": "{}",
                    "intent": "",
                    "observation": _trim_observation(f"forced_finish failed: {error}"),
                    "decision": "",
                }
            )
            await _emit_progress(
                progress_callback,
                stage="agentic_query_step",
                status="failed",
                message=f"Step {forced_step}: forced finish failed",
                metadata={
                    "runId": run_id,
                    "step": forced_step,
                    "action": "forced_finish_failed",
                    "tool": "forced_finish_failed",
                    "error": str(error),
                },
            )
            log_agentic_query_action(
                run_id=run_id,
                step=forced_step,
                action="forced_finish_failed",
                arguments={},
                result={},
                error=str(error),
            )

    # Terminal fallback: reached max-steps path and could not get valid forced finish.
    fallback_answer = "No answer found in the provided context."
    await _emit_progress(
        progress_callback,
        stage="agentic_query_pipeline",
        status="completed",
        message="Agentic query ended due to max step limit.",
        metadata={"runId": run_id, "toolCallCount": tool_call_count},
    )
    log_agentic_query_config_event(
        run_id=run_id,
        event="run_max_steps_config_usage",
        payload={
            "skills_md_path": str(config.skills_path),
            "skills_md_used_as_system_prompt": True,
            "references_read_count": len(used_reference_ids),
            "references_read_ids": sorted(used_reference_ids),
            "references_not_read_ids": sorted(
                set(config.reference_paths.keys()) - used_reference_ids
            ),
            "recent_step_trace": _summarize_step_trace_rows(step_traces),
        },
    )
    return AgenticQueryRunResult(
        answer=fallback_answer,
        citations=[],
        run_id=run_id,
        termination_reason="max_steps_exceeded",
        tool_call_count=tool_call_count,
    )


async def run_agentic_query(
    *,
    user_query: str,
    user_id: str,
    included_file_ids: list[str],
    seed_top_k: int = _DEFAULT_SEED_TOP_K,
    max_steps: int = _DEFAULT_MAX_STEPS,
    timeout_s: float = _DEFAULT_TIMEOUT_SECONDS,
    progress_callback: ProgressCallback | None = None,
) -> AgenticQueryRunResult:
    """Public entry point for one agentic query run.

    This function normalizes user inputs and applies a hard wall-clock timeout
    around `_run_loop` so the caller always receives a bounded response time.
    """
    normalized_query = str(user_query or "").strip()
    if not normalized_query:
        raise ValueError("user_query must be a non-empty string.")

    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        raise ValueError("user_id must be a non-empty string.")

    # Bound user-configurable knobs to safe ranges.
    normalized_seed_top_k = max(1, min(20, int(seed_top_k)))
    normalized_max_steps = max(_MIN_STEPS_CAP, min(_MAX_STEPS_CAP, int(max_steps)))
    normalized_timeout_s = max(5.0, float(timeout_s or _DEFAULT_TIMEOUT_SECONDS))

    run_id = uuid4().hex
    try:
        return await asyncio.wait_for(
            _run_loop(
                user_query=normalized_query,
                user_id=normalized_user_id,
                included_file_ids=[
                    str(file_id).strip()
                    for file_id in included_file_ids
                    if str(file_id).strip()
                ],
                seed_top_k=normalized_seed_top_k,
                max_steps=normalized_max_steps,
                run_id=run_id,
                timeout_s=normalized_timeout_s,
                progress_callback=progress_callback,
            ),
            timeout=normalized_timeout_s,
        )
    except TimeoutError:
        # Hard timeout fallback keeps API behavior deterministic under slow model/tool calls.
        log_agentic_query_action(
            run_id=run_id,
            step=None,
            action="runtime_timeout",
            arguments={},
            result={},
            error="Agentic query timed out.",
        )
        log_agentic_query_config_event(
            run_id=run_id,
            event="run_timeout_config_usage",
            payload={
                "skills_md_used_as_system_prompt": True,
                "timeout_seconds": normalized_timeout_s,
            },
        )
        await _emit_progress(
            progress_callback,
            stage="agentic_query_pipeline",
            status="failed",
            message="Agentic query timed out.",
            metadata={"runId": run_id},
        )
        return AgenticQueryRunResult(
            answer="No answer found in the provided context.",
            citations=[],
            run_id=run_id,
            termination_reason="timeout",
            tool_call_count=0,
        )
