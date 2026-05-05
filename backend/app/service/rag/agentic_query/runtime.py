"""Agentic query runtime with progressive skill loading.

The runtime keeps executable policy in Python and exposes markdown skill
content progressively: compact skill metadata is visible at startup, while full
skill bodies and references are loaded only through tools.
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
    FetchFileContextArguments,
    FetchParentChunkArguments,
    FinishArguments,
    LoadSkillArguments,
    ReadReferenceArguments,
    SearchContextArguments,
    SearchFilesArguments,
)

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]

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
_DEFAULT_MAX_STEPS = 12
_DEFAULT_SEED_TOP_K = 8
_MAX_STEPS_CAP = 12
_MIN_STEPS_CAP = 1
_MAX_TOOL_SUMMARY_CHARS = 1200
_MAX_TRACE_TEXT_CHARS = 240
_MAX_TRACE_ARGUMENTS_CHARS = 320
_MAX_TRACE_ROWS = 6
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", re.IGNORECASE)


def _safe_json_object(raw_text: str) -> dict[str, Any]:
    """Parse the first valid JSON object from raw model text."""

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


def _json_dumps(payload: Any) -> str:
    """Serialize compact JSON for transcript messages."""

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


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
        rendered = _json_dumps(arguments)
    except Exception:
        rendered = str(arguments)
    return _trim_observation(rendered, max_chars=max_chars)


def _summarize_evidence_cache(parent_doc_cache: dict[str, dict[str, Any]]) -> str:
    """Render a compact evidence summary for state messages and forced finish."""

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
        structured_view = str(doc.get("_agentic_query_structured_view") or "").strip()
        if structured_view:
            lines.append(
                f"- parent_id={parent_id}, file={file_name}, chunk={chunk_label}, "
                f"structured_view={structured_view}, snippet={snippet}"
            )
        else:
            lines.append(
                f"- parent_id={parent_id}, file={file_name}, chunk={chunk_label}, snippet={snippet}"
            )
    return "\n".join(lines) if lines else "- (no evidence cached yet)"


def _summarize_step_trace_rows(step_traces: list[dict[str, Any]]) -> str:
    """Render recent structured step traces."""

    if not step_traces:
        return "- (none yet)"

    rows: list[str] = []
    for trace in step_traces[-_MAX_TRACE_ROWS:]:
        step = trace.get("step")
        action = str(trace.get("action") or "").strip() or "unknown"
        arguments_preview = str(trace.get("arguments_preview") or "{}")
        observation = str(trace.get("observation") or "").strip() or "(no observation)"
        intent = str(trace.get("intent") or "").strip()
        fallback = str(trace.get("fallback") or trace.get("decision") or "").strip()

        row = f"- Step {step}: action={action}, args={arguments_preview}, observation={observation}"
        if intent:
            row += f", intent={intent}"
        if fallback:
            row += f", fallback={fallback}"
        rows.append(_trim_observation(row))
    return "\n".join(rows)


def _evidence_payload(items: list[Any]) -> list[dict[str, Any]]:
    """Serialize EvidenceItem models for transcript/log payloads."""

    payload: list[dict[str, Any]] = []
    for item in items:
        if hasattr(item, "model_dump"):
            payload.append(item.model_dump())
        elif isinstance(item, dict):
            payload.append(dict(item))
    return payload


def _tool_message_content(tool_name: str, payload: dict[str, Any]) -> str:
    """Build a compact tool result message."""

    return _json_dumps({"tool": tool_name, **payload})


def _append_tool_message(
    messages: list[dict[str, Any]],
    *,
    tool_name: str,
    payload: dict[str, Any],
    tool_call_id: str | None = None,
) -> None:
    """Append a tool role message to the persistent transcript."""

    message = {
        "role": "tool",
        "name": tool_name,
        "content": _tool_message_content(tool_name, payload),
    }
    if tool_call_id:
        message["tool_call_id"] = tool_call_id
    messages.append(message)


def _tool_call_id(run_id: str, step: int, action_name: str) -> str:
    """Create a stable tool-call id for JSON-protocol transcript messages."""

    safe_action = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(action_name or "tool"))
    return f"call_{run_id[:12]}_{step}_{safe_action}"


def _attach_tool_call(
    assistant_message: dict[str, Any],
    *,
    tool_call_id: str,
    action_name: str,
    arguments: dict[str, Any],
) -> None:
    """Add OpenAI-compatible tool-call metadata to an assistant action message."""

    assistant_message["tool_calls"] = [
        {
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": action_name,
                "arguments": _json_dumps(arguments),
            },
        }
    ]


def _assistant_message_from_model_result(
    content: str,
    model_result: Any,
) -> dict[str, Any]:
    """Build transcript assistant message while preserving provider thinking fields."""

    raw_message = getattr(model_result, "assistant_message", None)
    if isinstance(raw_message, dict):
        assistant_message = dict(raw_message)
        assistant_message["role"] = "assistant"
        assistant_message["content"] = content
        return assistant_message
    return {"role": "assistant", "content": content}


def _display_tool_name(action_name: str) -> str:
    """Return a user-facing label for an internal tool/action name."""

    labels = {
        "load_skill": "Load skill",
        "search_files": "Find files",
        "search_context": "Search documents",
        "fetch_parent_chunk": "Inspect source chunk",
        "fetch_file_context": "Read file context",
        "read_reference": "Read guidance",
        "finish": "Prepare answer",
        "forced_finish": "Prepare answer",
        "invalid_action_payload": "Repair action",
    }
    return labels.get(str(action_name or "").strip(), "Work step")


def _polished_transcript_item(
    *,
    role: str,
    title: str,
    summary: str,
    detail: str | None = None,
    status: str = "running",
) -> dict[str, Any]:
    """Build the progress transcript item shown in the frontend."""

    payload: dict[str, Any] = {
        "role": role,
        "title": _trim_observation(title, max_chars=80),
        "summary": _trim_observation(summary, max_chars=260),
        "status": status,
    }
    normalized_detail = _trace_text(detail, max_chars=360)
    if normalized_detail:
        payload["detail"] = normalized_detail
    return payload


def _action_transcript_item(action: AgentAction, *, step: int) -> dict[str, Any]:
    """Build a polished transcript item for an assistant action summary."""

    intent = _trace_text(action.intent) or f"Run {_display_tool_name(action.action).lower()}."
    fallback = _trace_text(action.fallback or action.decision)
    return _polished_transcript_item(
        role="assistant",
        title=f"Step {step}: {_display_tool_name(action.action)}",
        summary=intent,
        detail="Fallback: " + fallback if fallback else None,
        status="running",
    )


def _tool_transcript_item(
    *,
    action_name: str,
    step: int,
    observation: str,
    status: str,
) -> dict[str, Any]:
    """Build a polished transcript item for a tool result."""

    _ = step
    return _polished_transcript_item(
        role="tool",
        title=f"{_display_tool_name(action_name)} result",
        summary=observation,
        status=status,
    )


def _build_registry_message(config: Any) -> str:
    """Return compact startup skill metadata and action protocol."""

    registry = [
        metadata.as_registry_item()
        for metadata in sorted(config.skill_registry.values(), key=lambda item: item.name)
    ]
    return _json_dumps(
        {
            "runtime": "agentic_query",
            "progressive_disclosure": {
                "base_prompt_loaded": True,
                "skill_bodies_loaded": False,
                "references_loaded": False,
            },
            "skill_registry": registry,
            "action_protocol": {
                "shape": {
                    "intent": "short operational sentence",
                    "action": "load_skill|search_files|search_context|fetch_parent_chunk|fetch_file_context|read_reference|finish",
                    "arguments": {},
                    "success_criteria": "short condition for sufficiency",
                    "fallback": "short next step if insufficient",
                },
                "argument_shapes": {
                    "load_skill": {"skill_name": "agentic-query"},
                    "search_files": {"query": "filename terms", "limit": 5},
                    "search_context": {"query": "string", "top_k": 8},
                    "fetch_parent_chunk": {"parent_id": "string"},
                    "fetch_file_context": {"file_id": "string", "file_name": "optional string", "max_chunks": 20},
                    "read_reference": {"skill_name": "agentic-query", "ref_id": "answer_examples"},
                    "finish": {"answer": "string", "citations": ["file_name"]},
                },
                "rules": [
                    "Return only one JSON object per assistant turn.",
                    "Load a skill before relying on its detailed procedure.",
                    "For whole-file summaries, use fetch_file_context once a file_id is known.",
                    "Use finish on the final step or when evidence is sufficient.",
                ],
            },
        }
    )


def _build_state_update(
    *,
    user_query: str,
    step: int,
    max_steps: int,
    timeout_s: float,
    parent_doc_cache: dict[str, dict[str, Any]],
    step_traces: list[dict[str, Any]],
    pending_file_fetches: list[tuple[str, str]] | None = None,
) -> str:
    """Build a compact runtime state message for the next model turn."""

    file_candidate_hint = ""
    if pending_file_fetches:
        file_list = ", ".join(
            f"{name!r} (file_id={fid!r})" for fid, name in pending_file_fetches
        )
        file_candidate_hint = (
            f"\n\nKnown file candidate(s) not yet read: {file_list}. "
            "If the user is asking about one of these files, consider calling fetch_file_context "
            "with its file_id before continuing broad search.\n"
        )

    return (
        f"Runtime step {step}/{max_steps}. Timeout seconds: {timeout_s}.\n\n"
        f"User Query:\n{user_query}\n\n"
        "Current Evidence Cache:\n"
        f"{_summarize_evidence_cache(parent_doc_cache)}\n\n"
        "Recent Structured Step Trace:\n"
        f"{_summarize_step_trace_rows(step_traces)}"
        f"{file_candidate_hint}\n\n"
        "Return only the next JSON action-state object."
    )


def _messages_for_log(messages: list[dict[str, Any]]) -> str:
    """Serialize transcript for existing debug logger shape."""

    return json.dumps(messages, ensure_ascii=False, indent=2)


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
    included_file_ids: list[str] | None,
    seed_top_k: int,
    max_steps: int,
    run_id: str,
    timeout_s: float,
    progress_callback: ProgressCallback | None,
) -> AgenticQueryRunResult:
    """Execute the bounded agentic action loop for one query run."""

    config = load_agentic_query_config()
    cache_info = load_agentic_query_config.cache_info()
    parent_doc_cache: dict[str, dict[str, Any]] = {}
    observed_file_names: set[str] = set()
    loaded_skill_cache: dict[str, dict[str, Any]] = {}
    loaded_skill_names: set[str] = set()
    used_reference_ids: set[str] = set()
    step_traces: list[dict[str, Any]] = []
    tool_call_count = 0
    pending_file_fetches: list[tuple[str, str]] = []

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": config.system_prompt},
        {"role": "user", "content": _build_registry_message(config)},
        {
            "role": "user",
            "content": (
                f"User Query:\n{user_query}\n\n"
                "Use the skill registry metadata to decide whether to call load_skill. "
                "Do not rely on any full skill body until the load_skill tool returns it."
            ),
        },
    ]

    log_agentic_query_config_event(
        run_id=run_id,
        event="run_config_snapshot",
        payload={
            "system_path": str(config.system_path),
            "system_prompt_length": len(config.system_prompt),
            "skill_registry_loaded": True,
            "skill_metadata_exposed": [
                metadata.as_registry_item()
                for metadata in sorted(config.skill_registry.values(), key=lambda item: item.name)
            ],
            "skill_bodies_preloaded": False,
            "skill_paths": {name: str(path) for name, path in config.skill_paths.items()},
            "reference_paths": {
                skill: {ref_id: str(path) for ref_id, path in refs.items()}
                for skill, refs in config.reference_paths.items()
            },
            "deprecated_unused_paths": [str(path) for path in config.deprecated_paths],
            "included_file_ids_count": None if included_file_ids is None else len(included_file_ids),
            "search_scope": "all_collections" if included_file_ids is None else "collection",
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
        metadata={
            "runId": run_id,
            "transcriptMessage": _polished_transcript_item(
                role="system",
                title="Skill registry ready",
                summary="Loaded the base prompt and exposed compact skill metadata. Full skill instructions are still unloaded.",
                status="completed",
            ),
        },
    )

    seed_action = {
        "intent": "Seed the transcript with scoped evidence for the user query.",
        "action": "search_context",
        "arguments": {"query": user_query, "top_k": int(seed_top_k)},
        "success_criteria": "Initial scoped evidence is available to the assistant.",
        "fallback": "Continue with load_skill or another scoped search if evidence is insufficient.",
    }
    seed_tool_call_id = _tool_call_id(run_id, 0, "search_context")
    seed_assistant_message: dict[str, Any] = {"role": "assistant", "content": _json_dumps(seed_action)}
    _attach_tool_call(
        seed_assistant_message,
        tool_call_id=seed_tool_call_id,
        action_name="search_context",
        arguments=seed_action["arguments"],
    )
    messages.append(seed_assistant_message)
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
    seed_payload = {
        "status": "ok",
        "evidence_count": len(seed_evidence),
        "evidence": _evidence_payload(seed_evidence),
    }
    _append_tool_message(
        messages,
        tool_name="search_context",
        payload=seed_payload,
        tool_call_id=seed_tool_call_id,
    )
    log_agentic_query_action(
        run_id=run_id,
        step=0,
        action="seed_search_context",
        arguments={
            "query": user_query,
            "top_k": int(seed_top_k),
            "included_file_ids_count": None if included_file_ids is None else len(included_file_ids),
            "search_scope": "all_collections" if included_file_ids is None else "collection",
        },
        result=seed_payload,
    )
    await _emit_progress(
        progress_callback,
        stage="agentic_query_seed",
        status="completed",
        message="Initial retrieval seed completed.",
        metadata={
            "runId": run_id,
            "seedCount": len(seed_evidence),
            "transcriptMessage": _polished_transcript_item(
                role="tool",
                title="Initial document search",
                summary=f"Found {len(seed_evidence)} scoped evidence item{'s' if len(seed_evidence) != 1 else ''} before planning the next step.",
                status="completed",
            ),
        },
    )

    for step in range(1, max_steps + 1):
        messages.append(
            {
                "role": "user",
                "content": _build_state_update(
                    user_query=user_query,
                    step=step,
                    max_steps=max_steps,
                    timeout_s=timeout_s,
                    parent_doc_cache=parent_doc_cache,
                    step_traces=step_traces,
                    pending_file_fetches=pending_file_fetches if pending_file_fetches else None,
                ),
            }
        )
        log_agentic_query_llm_request(
            run_id=run_id,
            step=step,
            system_prompt=config.system_prompt,
            user_prompt=_messages_for_log(messages),
        )
        model_result = await llm_client.call_action_model(
            messages=messages,
            max_tokens=700,
            timeout_s=min(120.0, max(10.0, timeout_s)),
        )
        llm_response_text, _usage = model_result
        assistant_message = _assistant_message_from_model_result(
            llm_response_text,
            model_result,
        )
        messages.append(assistant_message)
        log_agentic_query_llm_response(
            run_id=run_id,
            step=step,
            response_text=llm_response_text,
        )

        try:
            action_payload = _safe_json_object(llm_response_text)
            action = AgentAction.model_validate(action_payload)
        except (ValueError, ValidationError) as error:
            invalid_observation = _trim_observation(f"Invalid action payload: {error}")
            messages.append(
                {
                    "role": "user",
                    "content": _tool_message_content(
                        "invalid_action_payload",
                        {"status": "error", "error": invalid_observation},
                    ),
                }
            )
            step_traces.append(
                {
                    "step": step,
                    "action": "invalid_action_payload",
                    "arguments_preview": "{}",
                    "intent": "",
                    "success_criteria": "",
                    "fallback": "",
                    "observation": invalid_observation,
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
                    "transcriptMessage": _polished_transcript_item(
                        role="tool",
                        title="Action needed correction",
                        summary="The assistant returned an action that could not be validated, so the runtime asked it to continue with a valid step.",
                        detail=invalid_observation,
                        status="failed",
                    ),
                },
            )
            continue

        trace_intent = _trace_text(action.intent)
        trace_success = _trace_text(action.success_criteria)
        trace_fallback = _trace_text(action.fallback or action.decision)
        arguments_preview = _arguments_preview(dict(action.arguments or {}))
        current_tool_call_id = _tool_call_id(run_id, step, action.action)
        _attach_tool_call(
            assistant_message,
            tool_call_id=current_tool_call_id,
            action_name=action.action,
            arguments=dict(action.arguments or {}),
        )
        step_observation = ""
        step_error: str | None = None
        tool_payload: dict[str, Any] = {"status": "ok"}

        log_agentic_query_config_event(
            run_id=run_id,
            event="assistant_action_summary",
            payload={
                "step": step,
                "intent": trace_intent,
                "action": action.action,
                "arguments": dict(action.arguments or {}),
                "success_criteria": trace_success,
                "fallback": trace_fallback,
            },
        )
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
                "successCriteria": trace_success,
                "fallback": trace_fallback,
                "decision": trace_fallback,
                "argumentsPreview": arguments_preview,
                "transcriptMessage": _action_transcript_item(action, step=step),
            },
        )

        try:
            if action.action == "load_skill":
                args = LoadSkillArguments.model_validate(action.arguments)
                payload = tools.load_skill_tool(
                    skill_name=args.skill_name,
                    config=config,
                    loaded_skill_cache=loaded_skill_cache,
                    max_chars=8000,
                )
                tool_call_count += 1
                loaded_skill_names.add(str(payload.get("skill_name") or args.skill_name).strip().lower())
                step_observation = _trim_observation(
                    f"load_skill loaded skill_name={payload.get('skill_name')} "
                    f"with {len(str(payload.get('body') or ''))} body chars."
                )
                tool_payload = {
                    "status": "ok",
                    "skill_name": payload.get("skill_name"),
                    "frontmatter": payload.get("frontmatter"),
                    "body": payload.get("body"),
                    "references": payload.get("references"),
                    "cached": bool(payload.get("cached")),
                }
                log_agentic_query_config_event(
                    run_id=run_id,
                    event="skill_body_loaded",
                    payload={
                        "step": step,
                        "skill_name": payload.get("skill_name"),
                        "cached": bool(payload.get("cached")),
                        "body_length": len(str(payload.get("body") or "")),
                        "references": payload.get("references") or [],
                    },
                )
            elif action.action == "search_files":
                args = SearchFilesArguments.model_validate(action.arguments)
                matches = await tools.search_files_tool(
                    query=args.query,
                    limit=args.limit,
                    user_id=user_id,
                    included_file_ids=included_file_ids,
                )
                tool_call_count += 1
                for match in matches:
                    observed_file_names.add(match.file_name)
                if matches:
                    pending_file_fetches = [(m.file_id, m.file_name) for m in matches]
                step_observation = _trim_observation(
                    "search_files returned "
                    f"{len(matches)} candidate files for query={args.query!r}."
                )
                tool_payload = {
                    "status": "ok",
                    "file_count": len(matches),
                    "files": _evidence_payload(matches),
                }
            elif action.action == "search_context":
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
                tool_payload = {
                    "status": "ok",
                    "evidence_count": len(evidence),
                    "evidence": _evidence_payload(evidence),
                }
            elif action.action == "fetch_parent_chunk":
                args = FetchParentChunkArguments.model_validate(action.arguments)
                item = await tools.fetch_parent_chunk_tool(
                    parent_id=args.parent_id,
                    user_id=user_id,
                    included_file_ids=included_file_ids,
                    parent_doc_cache=parent_doc_cache,
                    query=user_query,
                )
                tool_call_count += 1
                if item is None:
                    step_observation = "fetch_parent_chunk returned no scoped record."
                    tool_payload = {"status": "ok", "found": False}
                else:
                    observed_file_names.add(item.file_name)
                    step_observation = _trim_observation(
                        "fetch_parent_chunk returned "
                        f"parent_id={item.parent_id} in file={item.file_name}."
                    )
                    tool_payload = {
                        "status": "ok",
                        "found": True,
                        "evidence": item.model_dump(),
                    }
            elif action.action == "fetch_file_context":
                args = FetchFileContextArguments.model_validate(action.arguments)
                evidence = await tools.fetch_file_context_tool(
                    file_id=args.file_id,
                    file_name=args.file_name,
                    max_chunks=args.max_chunks,
                    user_id=user_id,
                    included_file_ids=included_file_ids,
                    parent_doc_cache=parent_doc_cache,
                    query=user_query,
                )
                tool_call_count += 1
                fetched_id = str(args.file_id or "").strip()
                pending_file_fetches = [
                    (fid, fname)
                    for fid, fname in pending_file_fetches
                    if fid != fetched_id
                ]
                for item in evidence:
                    observed_file_names.add(item.file_name)
                file_label = str(args.file_id or args.file_name or "").strip()
                step_observation = _trim_observation(
                    "fetch_file_context returned "
                    f"{len(evidence)} parent chunks for file={file_label!r}."
                )
                tool_payload = {
                    "status": "ok",
                    "evidence_count": len(evidence),
                    "evidence": _evidence_payload(evidence),
                }
            elif action.action == "read_reference":
                args = ReadReferenceArguments.model_validate(action.arguments)
                ref_text = tools.read_reference_tool(
                    skill_name=args.skill_name,
                    ref_id=args.ref_id,
                    config=config,
                    max_chars=2500,
                )
                tool_call_count += 1
                ref_key = f"{str(args.skill_name).strip().lower().replace('_', '-')}:{str(args.ref_id).strip().lower()}"
                used_reference_ids.add(ref_key)
                step_observation = _trim_observation(
                    f"read_reference loaded skill_name={args.skill_name} ref_id={args.ref_id} "
                    f"with {len(ref_text)} chars."
                )
                tool_payload = {
                    "status": "ok",
                    "skill_name": args.skill_name,
                    "ref_id": args.ref_id,
                    "content": ref_text,
                }
                log_agentic_query_config_event(
                    run_id=run_id,
                    event="reference_loaded",
                    payload={
                        "step": step,
                        "skill_name": args.skill_name,
                        "ref_id": args.ref_id,
                        "content_length": len(ref_text),
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
                if not normalized_citations:
                    normalized_citations = sorted(observed_file_names)
                step_observation = _trim_observation(
                    "finish returned final answer with "
                    f"{len(normalized_citations)} citations."
                )
                tool_payload = {
                    "status": "ok",
                    "answer_length": len(normalized_answer),
                    "citations": normalized_citations,
                }
                log_agentic_query_action(
                    run_id=run_id,
                    step=step,
                    action="finish",
                    arguments={
                        "citations_from_model": list(args.citations),
                        "intent": trace_intent,
                        "success_criteria": trace_success,
                        "fallback": trace_fallback,
                    },
                    result={
                        "final_answer_length": len(normalized_answer),
                        "final_citations": list(normalized_citations),
                    },
                )
                _append_tool_message(
                    messages,
                    tool_name="finish",
                    payload=tool_payload,
                    tool_call_id=current_tool_call_id,
                )
                step_traces.append(
                    {
                        "step": step,
                        "action": action.action,
                        "arguments_preview": arguments_preview,
                        "intent": trace_intent or "",
                        "success_criteria": trace_success or "",
                        "fallback": trace_fallback or "",
                        "observation": step_observation,
                    }
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
                        "successCriteria": trace_success,
                        "fallback": trace_fallback,
                        "decision": trace_fallback,
                        "argumentsPreview": arguments_preview,
                        "observation": step_observation,
                        "transcriptMessage": _tool_transcript_item(
                            action_name=action.action,
                            step=step,
                            observation=step_observation,
                            status="completed",
                        ),
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
                        "system_path": str(config.system_path),
                        "skill_bodies_preloaded": False,
                        "loaded_skill_names": sorted(loaded_skill_names),
                        "references_read_count": len(used_reference_ids),
                        "references_read_ids": sorted(used_reference_ids),
                        "recent_step_trace": _summarize_step_trace_rows(step_traces),
                        "termination_reason": "finished",
                    },
                )
                return AgenticQueryRunResult(
                    answer=normalized_answer,
                    citations=normalized_citations,
                    run_id=run_id,
                    termination_reason="finished",
                    tool_call_count=tool_call_count,
                )
        except Exception as error:
            step_error = str(error)
            step_observation = _trim_observation(f"Action execution failed: {error}")
            tool_payload = {"status": "error", "error": step_observation}

        if not step_observation:
            step_observation = "Action completed."
        if action.action != "finish":
            _append_tool_message(
                messages,
                tool_name=action.action,
                payload=tool_payload,
                tool_call_id=current_tool_call_id,
            )
            log_agentic_query_action(
                run_id=run_id,
                step=step,
                action=action.action,
                arguments={
                    **dict(action.arguments or {}),
                    "intent": trace_intent,
                    "success_criteria": trace_success,
                    "fallback": trace_fallback,
                },
                result=tool_payload,
                error=step_error,
            )
            step_traces.append(
                {
                    "step": step,
                    "action": action.action,
                    "arguments_preview": arguments_preview,
                    "intent": trace_intent or "",
                    "success_criteria": trace_success or "",
                    "fallback": trace_fallback or "",
                    "observation": step_observation,
                }
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
                    "successCriteria": trace_success,
                    "fallback": trace_fallback,
                    "decision": trace_fallback,
                    "argumentsPreview": arguments_preview,
                    "observation": step_observation,
                    "error": step_error,
                    "transcriptMessage": _tool_transcript_item(
                        action_name=action.action,
                        step=step,
                        observation=step_observation,
                        status="failed" if step_error else "completed",
                    ),
                },
            )

    if parent_doc_cache:
        forced_step = max_steps + 1
        messages.append(
            {
                "role": "user",
                "content": (
                    "No more tool calls are allowed. Use only observed evidence in this transcript "
                    "and return a finish JSON object. If evidence does not answer the query, use "
                    'answer "No answer found in the provided context." and empty citations.\n\n'
                    "Current Evidence Cache:\n"
                    f"{_summarize_evidence_cache(parent_doc_cache)}"
                ),
            }
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
                "transcriptMessage": _polished_transcript_item(
                    role="assistant",
                    title="Final answer synthesis",
                    summary="Reached the step limit, so the assistant is preparing an answer from the evidence already gathered.",
                    status="running",
                ),
            },
        )
        log_agentic_query_llm_request(
            run_id=run_id,
            step=forced_step,
            system_prompt=config.system_prompt,
            user_prompt=_messages_for_log(messages),
        )
        try:
            model_result = await llm_client.call_action_model(
                messages=messages,
                max_tokens=700,
                timeout_s=min(120.0, max(10.0, timeout_s)),
            )
            llm_response_text, _usage = model_result
            forced_assistant_message = _assistant_message_from_model_result(
                llm_response_text,
                model_result,
            )
            messages.append(forced_assistant_message)
            log_agentic_query_llm_response(
                run_id=run_id,
                step=forced_step,
                response_text=llm_response_text,
            )
            action_payload = _safe_json_object(llm_response_text)
            action = AgentAction.model_validate(action_payload)
            if action.action == "finish":
                forced_tool_call_id = _tool_call_id(run_id, forced_step, "finish")
                _attach_tool_call(
                    forced_assistant_message,
                    tool_call_id=forced_tool_call_id,
                    action_name="finish",
                    arguments=dict(action.arguments or {}),
                )
                args = FinishArguments.model_validate(action.arguments)
                normalized_answer = str(args.answer or "").strip()
                if not normalized_answer:
                    normalized_answer = "No answer found in the provided context."
                normalized_citations = _normalize_citations(
                    args.citations,
                    allowed_file_names=observed_file_names,
                )
                if not normalized_citations and normalized_answer != "No answer found in the provided context.":
                    normalized_citations = sorted(observed_file_names)
                forced_intent = _trace_text(action.intent)
                forced_success = _trace_text(action.success_criteria)
                forced_fallback = _trace_text(action.fallback or action.decision)
                forced_observation = _trim_observation(
                    "forced_finish produced final answer with "
                    f"{len(normalized_citations)} citations."
                )
                _append_tool_message(
                    messages,
                    tool_name="finish",
                    payload={
                        "status": "ok",
                        "answer_length": len(normalized_answer),
                        "citations": normalized_citations,
                    },
                    tool_call_id=forced_tool_call_id,
                )
                log_agentic_query_action(
                    run_id=run_id,
                    step=forced_step,
                    action="forced_finish",
                    arguments={
                        "citations_from_model": list(args.citations),
                        "intent": forced_intent,
                        "success_criteria": forced_success,
                        "fallback": forced_fallback,
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
                        "success_criteria": forced_success or "",
                        "fallback": forced_fallback or "",
                        "observation": forced_observation,
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
                        "successCriteria": forced_success,
                        "fallback": forced_fallback,
                        "decision": forced_fallback,
                        "argumentsPreview": _arguments_preview(dict(action.arguments or {})),
                        "observation": forced_observation,
                        "transcriptMessage": _tool_transcript_item(
                            action_name="forced_finish",
                            step=forced_step,
                            observation=forced_observation,
                            status="completed",
                        ),
                    },
                )
                log_agentic_query_config_event(
                    run_id=run_id,
                    event="run_forced_finish_config_usage",
                    payload={
                        "system_path": str(config.system_path),
                        "skill_bodies_preloaded": False,
                        "loaded_skill_names": sorted(loaded_skill_names),
                        "references_read_count": len(used_reference_ids),
                        "references_read_ids": sorted(used_reference_ids),
                        "recent_step_trace": _summarize_step_trace_rows(step_traces),
                        "termination_reason": "forced_finish_after_max_steps",
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
                    "success_criteria": "",
                    "fallback": "",
                    "observation": _trim_observation(f"forced_finish failed: {error}"),
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
                    "transcriptMessage": _polished_transcript_item(
                        role="tool",
                        title="Final synthesis failed",
                        summary="The forced finish step failed, so the runtime will return the safe no-answer fallback.",
                        detail=str(error),
                        status="failed",
                    ),
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
            "system_path": str(config.system_path),
            "skill_bodies_preloaded": False,
            "loaded_skill_names": sorted(loaded_skill_names),
            "references_read_count": len(used_reference_ids),
            "references_read_ids": sorted(used_reference_ids),
            "recent_step_trace": _summarize_step_trace_rows(step_traces),
            "termination_reason": "max_steps_exceeded",
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
    included_file_ids: list[str] | None,
    seed_top_k: int = _DEFAULT_SEED_TOP_K,
    max_steps: int = _DEFAULT_MAX_STEPS,
    timeout_s: float = _DEFAULT_TIMEOUT_SECONDS,
    progress_callback: ProgressCallback | None = None,
) -> AgenticQueryRunResult:
    """Public entry point for one agentic query run."""

    normalized_query = str(user_query or "").strip()
    if not normalized_query:
        raise ValueError("user_query must be a non-empty string.")

    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        raise ValueError("user_id must be a non-empty string.")

    normalized_seed_top_k = max(1, min(20, int(seed_top_k)))
    normalized_max_steps = max(_MIN_STEPS_CAP, min(_MAX_STEPS_CAP, int(max_steps)))
    normalized_timeout_s = max(5.0, float(timeout_s or _DEFAULT_TIMEOUT_SECONDS))
    normalized_file_ids = (
        None
        if included_file_ids is None
        else [
            str(file_id).strip()
            for file_id in included_file_ids
            if str(file_id).strip()
        ]
    )

    run_id = uuid4().hex
    try:
        return await asyncio.wait_for(
            _run_loop(
                user_query=normalized_query,
                user_id=normalized_user_id,
                included_file_ids=normalized_file_ids,
                seed_top_k=normalized_seed_top_k,
                max_steps=normalized_max_steps,
                run_id=run_id,
                timeout_s=normalized_timeout_s,
                progress_callback=progress_callback,
            ),
            timeout=normalized_timeout_s,
        )
    except TimeoutError:
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
                "system_prompt_only": True,
                "skill_bodies_preloaded": False,
                "timeout_seconds": normalized_timeout_s,
                "termination_reason": "timeout",
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
