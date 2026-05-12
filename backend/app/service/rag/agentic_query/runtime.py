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
    FindInventoryRecordsArguments,
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


_DEFAULT_TIMEOUT_SECONDS = 500.0
_DEFAULT_MAX_STEPS = 12
_DEFAULT_SEED_TOP_K = 8
_MAX_STEPS_CAP = 12
_MIN_STEPS_CAP = 1
_MAX_TOOL_SUMMARY_CHARS = 1200
_MAX_TRACE_TEXT_CHARS = 240
_MAX_TRACE_ARGUMENTS_CHARS = 320
_MAX_TRACE_ROWS = 6
_MAX_STATE_EVIDENCE_ROWS = 12
_MAX_STATE_SNIPPET_CHARS = 420
_MAX_TOOL_EVIDENCE_SNIPPET_CHARS = 900
_MAX_TOOL_EVIDENCE_STRUCTURED_CHARS = 900
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", re.IGNORECASE)
_ACTION_ALIASES = {
    "load_skill": "load_answering_instructions",
    "search_files": "find_files_by_name",
    "find_module_overviews": "find_inventory_records",
    "search_context": "search_relevant_chunks",
    "fetch_parent_chunk": "read_chunk_detail",
    "fetch_file_context": "read_file_chunks",
    "read_reference": "read_skill_reference",
    "finish": "provide_final_answer",
}


def _inventory_seed_request(user_query: str) -> dict[str, Any] | None:
    """Return a deterministic inventory seed request for exhaustive list questions."""

    normalized = " ".join(str(user_query or "").split()).lower()
    if not normalized:
        return None

    asks_for_inventory = any(
        phrase in normalized
        for phrase in (
            "all module",
            "all the module",
            "all modules",
            "all the modules",
            "all ",
            "list ",
            "list module",
            "list down",
            "find all",
            "show all",
            "every module",
            "every ",
        )
    )
    if not asks_for_inventory:
        return None

    return {"query": str(user_query or "").strip(), "max_matches": 50}
_TOOL_DESCRIPTIONS: dict[str, dict[str, Any]] = {
    "load_answering_instructions": {
        "purpose": "Load the full answer-writing and retrieval instructions for one skill before relying on that skill's detailed procedure.",
        "use_when": "Use early when the skill registry says a skill is relevant and only its short metadata is currently visible.",
        "inputs": {"skill_name": "Skill name from the registry, usually agentic-query."},
        "returns": "Full bounded skill body, frontmatter, available reference IDs, and cache status.",
    },
    "find_files_by_name": {
        "purpose": "Find in-scope files by filename and return file IDs that can be used for whole-file reads.",
        "use_when": "Use when the user names or implies a specific file, asks about an entire file, or content search hints at a target file but the file_id is unknown.",
        "inputs": {"query": "Filename terms or a likely document title.", "limit": "Maximum file matches to return, capped by the runtime."},
        "returns": "Matching file_id, file_name, first parent chunk ID, and a short file preview.",
    },
    "find_inventory_records": {
        "purpose": "Deterministically scan scoped files for item records matching an exhaustive list-style query.",
        "use_when": "Use when the user asks for all/every/listed items. Semantic search alone is ranked, so it may miss sibling records in inventories.",
        "inputs": {"query": "Original user query or a concise inventory query.", "max_matches": "Maximum matching records to return, capped by the runtime."},
        "returns": "Evidence items from matching parent chunks, deduplicated by file when possible.",
    },
    "search_relevant_chunks": {
        "purpose": "Run semantic retrieval over the allowed user/collection scope and return the most relevant parent chunks.",
        "use_when": "Use for normal fact-finding, targeted follow-up searches, or when the answer should come from relevant passages rather than an entire file.",
        "inputs": {"query": "Search query text.", "top_k": "Requested number of parent chunks, bounded to the runtime limit."},
        "returns": "Evidence items with parent_id, file_id, file_name, chunk number, a query-focused snippet, and structured table view when available.",
    },
    "read_chunk_detail": {
        "purpose": "Inspect one known parent chunk more deeply by parent_id.",
        "use_when": "Use after search_relevant_chunks or find_files_by_name has exposed a specific parent_id and the short snippet is not enough to answer confidently.",
        "inputs": {"parent_id": "Parent chunk ID from earlier evidence."},
        "returns": "A larger snippet for that one parent chunk, plus source metadata and structured table view when available.",
    },
    "read_file_chunks": {
        "purpose": "Read ordered parent chunks from one in-scope file.",
        "use_when": "Use when the user asks to summarize, audit, compare, extract requirements from, or answer broadly about a whole file. Prefer file_id when known.",
        "inputs": {"file_id": "Preferred exact file ID.", "file_name": "Optional filename fallback if file_id is unknown.", "max_chunks": "Maximum ordered chunks to read, bounded by the runtime."},
        "returns": "A sequence of evidence items from the file in chunk order. Each item is still bounded as a snippet to keep the prompt within context limits.",
    },
    "read_skill_reference": {
        "purpose": "Load optional markdown guidance or examples for the active skill.",
        "use_when": "Use only when examples or policy guidance are needed to answer or cite correctly.",
        "inputs": {"skill_name": "Skill name.", "ref_id": "Reference ID listed in the skill registry."},
        "returns": "Bounded markdown reference text.",
    },
    "provide_final_answer": {
        "purpose": "End the run with the final answer and file-name citations.",
        "use_when": "Use as soon as observed evidence is sufficient, or when evidence is insufficient and the no-answer fallback is required.",
        "inputs": {"answer": "Final plain-text answer.", "citations": "List of observed supporting file names only."},
        "returns": "Terminal answer payload; no more tools are called.",
    },
}


def _canonical_action_name(action_name: str) -> str:
    normalized = str(action_name or "").strip()
    return _ACTION_ALIASES.get(normalized, normalized)


def _safe_json_object(raw_text: str) -> dict[str, Any]:
    """Parse the first valid JSON object from raw model text."""

    stripped = str(raw_text or "").strip()
    if not stripped:
        raise ValueError("Model returned empty action payload.")
    decoder = json.JSONDecoder()

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = _JSON_BLOCK_PATTERN.search(stripped)
    if fenced:
        fenced_content = fenced.group(1)
        for start_index, char in enumerate(fenced_content):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(fenced_content[start_index:])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue

    for start_index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped[start_index:])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

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


def _extract_record_codes(raw: str | None) -> set[str]:
    """Extract module/course-style codes from answer text or file names."""

    return {
        match.group(0).upper()
        for match in re.finditer(r"\b[A-Z]{2,}\d{3,4}\b", str(raw or ""), flags=re.IGNORECASE)
    }


def _record_codes_from_file_names(file_names: set[str]) -> set[str]:
    codes: set[str] = set()
    for file_name in file_names:
        codes.update(_extract_record_codes(file_name))
    return codes


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
    """Render a compact evidence ledger for state messages and forced finish.

    Full parent documents stay in ``parent_doc_cache`` for tools that need them.
    The LLM only needs a concise ledger on each turn; replaying full snippets
    repeatedly causes later searches to compete with stale context.
    """

    lines: list[str] = []
    for index, (parent_id, doc) in enumerate(parent_doc_cache.items(), start=1):
        if index > _MAX_STATE_EVIDENCE_ROWS:
            remaining = len(parent_doc_cache) - _MAX_STATE_EVIDENCE_ROWS
            lines.append(f"- ... {remaining} more cached evidence item(s) not repeated in this state update")
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
        raw_snippet = cached_snippet or " ".join(str(doc.get("page_content") or "").split())
        snippet = _trim_observation(raw_snippet, max_chars=_MAX_STATE_SNIPPET_CHARS)
        structured_view = _trim_observation(
            str(doc.get("_agentic_query_structured_view") or "").strip(),
            max_chars=_MAX_STATE_SNIPPET_CHARS,
        )
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


def _compact_evidence_payload(items: list[Any]) -> list[dict[str, Any]]:
    """Serialize evidence for the LLM transcript without replaying full chunks."""

    payload = _evidence_payload(items)
    compact_payload: list[dict[str, Any]] = []
    for item in payload:
        compact_item = dict(item)
        compact_item["snippet"] = _trim_observation(
            str(compact_item.get("snippet") or ""),
            max_chars=_MAX_TOOL_EVIDENCE_SNIPPET_CHARS,
        )
        compact_item["structured_view"] = _trim_observation(
            str(compact_item.get("structured_view") or ""),
            max_chars=_MAX_TOOL_EVIDENCE_STRUCTURED_CHARS,
        )
        compact_payload.append(compact_item)
    return compact_payload


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
        "load_answering_instructions": "Load answer instructions",
        "find_files_by_name": "Find files by name",
        "find_inventory_records": "Find inventory records",
        "search_relevant_chunks": "Search relevant chunks",
        "read_chunk_detail": "Read chunk detail",
        "read_file_chunks": "Read file chunks",
        "read_skill_reference": "Read skill guidance",
        "provide_final_answer": "Prepare answer",
        "forced_finish": "Prepare answer",
        "invalid_action_payload": "Repair action",
    }
    return labels.get(_canonical_action_name(action_name), "Work step")


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
                "available_tools": _TOOL_DESCRIPTIONS,
                "shape": {
                    "intent": "short operational sentence",
                    "action": "load_answering_instructions|find_files_by_name|find_inventory_records|search_relevant_chunks|read_chunk_detail|read_file_chunks|read_skill_reference|provide_final_answer",
                    "arguments": {},
                    "success_criteria": "short condition for sufficiency",
                    "fallback": "short next step if insufficient",
                },
                "argument_shapes": {
                    "load_answering_instructions": {"skill_name": "agentic-query"},
                    "find_files_by_name": {"query": "filename terms", "limit": 5},
                    "find_inventory_records": {"query": "original list-style user query", "max_matches": 50},
                    "search_relevant_chunks": {"query": "string", "top_k": 8},
                    "read_chunk_detail": {"parent_id": "string"},
                    "read_file_chunks": {"file_id": "string", "file_name": "optional string", "max_chunks": 20},
                    "read_skill_reference": {"skill_name": "agentic-query", "ref_id": "answer_examples"},
                    "provide_final_answer": {"answer": "string", "citations": ["file_name"]},
                },
                "rules": [
                    "Return only one JSON object per assistant turn.",
                    "Use load_answering_instructions before relying on a skill's detailed procedure.",
                    "For exhaustive list/inventory questions, use find_inventory_records before finalizing.",
                    "For whole-file summaries, use read_file_chunks once a file_id is known.",
                    "Use provide_final_answer on the final step or when evidence is sufficient.",
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
    inventory_baseline_file_names: set[str] | None = None,
) -> str:
    """Build a compact runtime state message for the next model turn."""

    file_candidate_hint = ""
    if pending_file_fetches:
        file_list = ", ".join(
            f"{name!r} (file_id={fid!r})" for fid, name in pending_file_fetches
        )
        file_candidate_hint = (
            f"\n\nKnown file candidate(s) not yet read: {file_list}. "
            "If the user is asking about one of these files, consider calling read_file_chunks "
            "with its file_id before continuing broad search.\n"
        )

    inventory_baseline_hint = ""
    if inventory_baseline_file_names:
        baseline_files = ", ".join(sorted(inventory_baseline_file_names))
        inventory_baseline_hint = (
            "\n\nInventory baseline for this exhaustive query:\n"
            f"{baseline_files}\n"
            "Final inventory answers must include exactly these inventory items; "
            "semantic-search evidence can add details but cannot add or remove inventory items.\n"
        )

    return (
        f"Runtime step {step}/{max_steps}. Timeout seconds: {timeout_s}.\n\n"
        f"User Query:\n{user_query}\n\n"
        "Current Evidence Cache:\n"
        f"{_summarize_evidence_cache(parent_doc_cache)}\n\n"
        "Recent Structured Step Trace:\n"
        f"{_summarize_step_trace_rows(step_traces)}"
        f"{file_candidate_hint}"
        f"{inventory_baseline_hint}\n\n"
        "Return only the next JSON action-state object."
    )


def _drop_prior_state_updates(messages: list[dict[str, Any]]) -> None:
    """Remove obsolete runtime state snapshots before appending the next one."""

    messages[:] = [
        message
        for message in messages
        if not (
            message.get("role") == "user"
            and str(message.get("content") or "").startswith("Runtime step ")
        )
    ]


def _messages_for_log(messages: list[dict[str, Any]]) -> str:
    """Render the model transcript in a human-readable debug-log format."""

    def _append_mapping(prefix: str, payload: dict[str, Any]) -> None:
        for key, value in payload.items():
            label = str(key).replace("_", " ").title()
            if isinstance(value, (dict, list)):
                rendered = json.dumps(value, ensure_ascii=False, indent=2)
                lines.append(f"{prefix}{label}:")
                lines.append(
                    rendered[:1400] + "\n...[truncated]"
                    if len(rendered) > 1400
                    else rendered
                )
            else:
                text = " ".join(str(value).split())
                lines.append(f"{prefix}{label}: {text if text else 'N/A'}")

    lines: list[str] = []
    for index, message in enumerate(messages, start=1):
        role = str(message.get("role") or "unknown").upper()
        name = str(message.get("name") or "").strip()
        title = f"Message {index}: {role}"
        if name:
            title += f" ({name})"
        lines.append(title)
        lines.append("-" * len(title))

        content = str(message.get("content") or "").strip()
        parsed_content: dict[str, Any] | None = None
        if content.startswith("{") and content.endswith("}"):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    parsed_content = parsed
            except json.JSONDecodeError:
                parsed_content = None

        if parsed_content and "action" in parsed_content:
            action = str(parsed_content.get("action") or "unknown")
            intent = _trace_text(parsed_content.get("intent"))
            success = _trace_text(parsed_content.get("success_criteria"))
            fallback = _trace_text(parsed_content.get("fallback") or parsed_content.get("decision"))
            arguments = parsed_content.get("arguments") if isinstance(parsed_content.get("arguments"), dict) else {}
            if intent:
                lines.append(f"Intent: {intent}")
            lines.append(f"Action: {action}")
            if arguments:
                lines.append("Arguments:")
                _append_mapping("- ", arguments)
            if success:
                lines.append(f"Success criteria: {success}")
            if fallback:
                lines.append(f"Fallback: {fallback}")
        elif parsed_content and "tool" in parsed_content:
            tool = str(parsed_content.get("tool") or name or "tool")
            status = str(parsed_content.get("status") or "unknown")
            lines.append(f"Tool: {tool}")
            lines.append(f"Status: {status}")
            if "error" in parsed_content:
                lines.append(f"Error: {_trace_text(parsed_content.get('error'), max_chars=900) or 'N/A'}")
            for key in ("evidence_count", "file_count", "answer_length"):
                if key in parsed_content:
                    lines.append(f"{key.replace('_', ' ').title()}: {parsed_content[key]}")
            evidence = parsed_content.get("evidence")
            if isinstance(evidence, list) and evidence:
                lines.append("Evidence:")
                for item_index, item in enumerate(evidence[:6], start=1):
                    if not isinstance(item, dict):
                        continue
                    file_name = item.get("file_name") or "unknown file"
                    parent_id = item.get("parent_id") or item.get("id") or "N/A"
                    snippet = _trace_text(item.get("snippet") or item.get("content"), max_chars=500)
                    lines.append(f"- {item_index}. {file_name} (parent_id={parent_id})")
                    if snippet:
                        lines.append(f"  Snippet: {snippet}")
                if len(evidence) > 6:
                    lines.append(f"- ... {len(evidence) - 6} more evidence item(s)")
            elif "content" in parsed_content:
                lines.append("Content:")
                tool_content = str(parsed_content.get("content") or "")
                lines.append(
                    tool_content[:2000] + "\n...[truncated]"
                    if len(tool_content) > 2000
                    else tool_content
                )
            else:
                omitted = {
                    key: value
                    for key, value in parsed_content.items()
                    if key not in {"tool", "status", "evidence", "error", "content"}
                }
                if omitted:
                    lines.append("Payload:")
                    _append_mapping("- ", omitted)
        else:
            lines.append(content[:4000] + "\n...[truncated]" if len(content) > 4000 else content or "(empty)")

        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            lines.append("Tool call metadata:")
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                lines.append(
                    f"- {function.get('name') or 'unknown'} "
                    f"(id={call.get('id') or 'N/A'})"
                )
        lines.append("")
    return "\n".join(lines).strip()


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
    inventory_baseline_file_names: set[str] = set()

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": config.system_prompt},
        {"role": "user", "content": _build_registry_message(config)},
        {
            "role": "user",
            "content": (
                f"User Query:\n{user_query}\n\n"
                "Use the skill registry metadata to decide whether to call load_answering_instructions. "
                "Do not rely on any full skill body until the load_answering_instructions tool returns it."
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
        "action": "search_relevant_chunks",
        "arguments": {"query": user_query, "top_k": int(seed_top_k)},
        "success_criteria": "Initial scoped evidence is available to the assistant.",
        "fallback": "Continue with load_answering_instructions or another scoped search if evidence is insufficient.",
    }
    seed_tool_call_id = _tool_call_id(run_id, 0, "search_relevant_chunks")
    seed_assistant_message: dict[str, Any] = {"role": "assistant", "content": _json_dumps(seed_action)}
    _attach_tool_call(
        seed_assistant_message,
        tool_call_id=seed_tool_call_id,
        action_name="search_relevant_chunks",
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
        "evidence": _compact_evidence_payload(seed_evidence),
    }
    _append_tool_message(
        messages,
        tool_name="search_relevant_chunks",
        payload=seed_payload,
        tool_call_id=seed_tool_call_id,
    )
    log_agentic_query_action(
        run_id=run_id,
        step=0,
        action="seed_search_relevant_chunks",
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

    inventory_seed_request = _inventory_seed_request(user_query)
    if inventory_seed_request:
        inventory_seed_action = {
            "intent": "Broaden exhaustive list evidence beyond ranked semantic retrieval.",
            "action": "find_inventory_records",
            "arguments": inventory_seed_request,
            "success_criteria": "Scoped inventory records are available for list-style answering.",
            "fallback": "Continue with the normal agentic loop if no inventory records are found.",
        }
        inventory_seed_tool_call_id = _tool_call_id(run_id, 0, "find_inventory_records")
        inventory_seed_assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": _json_dumps(inventory_seed_action),
        }
        _attach_tool_call(
            inventory_seed_assistant_message,
            tool_call_id=inventory_seed_tool_call_id,
            action_name="find_inventory_records",
            arguments=inventory_seed_action["arguments"],
        )
        messages.append(inventory_seed_assistant_message)
        try:
            inventory_evidence = await tools.find_inventory_records_tool(
                query=str(inventory_seed_request.get("query") or user_query),
                user_id=user_id,
                included_file_ids=included_file_ids,
                parent_doc_cache=parent_doc_cache,
                max_matches=int(inventory_seed_request.get("max_matches") or 50),
            )
            inventory_seed_status = "ok"
            inventory_seed_error = None
        except Exception as exc:
            inventory_evidence = []
            inventory_seed_status = "error"
            inventory_seed_error = str(exc)
        tool_call_count += 1
        for item in inventory_evidence:
            observed_file_names.add(item.file_name)
            inventory_baseline_file_names.add(item.file_name)
        inventory_seed_payload: dict[str, Any] = {
            "status": inventory_seed_status,
            "evidence_count": len(inventory_evidence),
            "evidence": _compact_evidence_payload(inventory_evidence),
        }
        if inventory_seed_error:
            inventory_seed_payload["error"] = inventory_seed_error
        _append_tool_message(
            messages,
            tool_name="find_inventory_records",
            payload=inventory_seed_payload,
            tool_call_id=inventory_seed_tool_call_id,
        )
        log_agentic_query_action(
            run_id=run_id,
            step=0,
            action="seed_find_inventory_records",
            arguments={
                **inventory_seed_request,
                "included_file_ids_count": None if included_file_ids is None else len(included_file_ids),
                "search_scope": "all_collections" if included_file_ids is None else "collection",
            },
            result=inventory_seed_payload,
        )
        await _emit_progress(
            progress_callback,
            stage="agentic_query_seed",
            status="completed" if inventory_seed_status == "ok" else "warning",
            message="Inventory seed completed.",
            metadata={
                "runId": run_id,
                "seedCount": len(inventory_evidence),
                "transcriptMessage": _polished_transcript_item(
                    role="tool",
                    title="Inventory scan",
                    summary=(
                        f"Found {len(inventory_evidence)} scoped inventory "
                        f"record{'s' if len(inventory_evidence) != 1 else ''} for the requested list."
                    ),
                    status="completed" if inventory_seed_status == "ok" else "warning",
                ),
            },
        )

    for step in range(1, max_steps + 1):
        _drop_prior_state_updates(messages)
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
                    inventory_baseline_file_names=(
                        inventory_baseline_file_names
                        if inventory_baseline_file_names
                        else None
                    ),
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
            action.action = _canonical_action_name(action.action)
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
            if action.action == "load_answering_instructions":
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
                    f"load_answering_instructions loaded skill_name={payload.get('skill_name')} "
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
            elif action.action == "find_files_by_name":
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
                    "find_files_by_name returned "
                    f"{len(matches)} candidate files for query={args.query!r}."
                )
                tool_payload = {
                    "status": "ok",
                    "file_count": len(matches),
                    "files": _evidence_payload(matches),
                }
            elif action.action == "find_inventory_records":
                raw_arguments = dict(action.arguments or {})
                if "query" not in raw_arguments:
                    raw_arguments["query"] = user_query
                args = FindInventoryRecordsArguments.model_validate(raw_arguments)
                evidence = await tools.find_inventory_records_tool(
                    query=args.query,
                    user_id=user_id,
                    included_file_ids=included_file_ids,
                    parent_doc_cache=parent_doc_cache,
                    max_matches=args.max_matches,
                )
                tool_call_count += 1
                for item in evidence:
                    observed_file_names.add(item.file_name)
                    inventory_baseline_file_names.add(item.file_name)
                step_observation = _trim_observation(
                    "find_inventory_records returned "
                    f"{len(evidence)} inventory records for query={args.query!r}."
                )
                tool_payload = {
                    "status": "ok",
                    "evidence_count": len(evidence),
                    "evidence": _compact_evidence_payload(evidence),
                }
            elif action.action == "search_relevant_chunks":
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
                    "search_relevant_chunks returned "
                    f"{len(evidence)} items for query={args.query!r} top_k={int(args.top_k)}."
                )
                tool_payload = {
                    "status": "ok",
                    "evidence_count": len(evidence),
                    "evidence": _compact_evidence_payload(evidence),
                }
            elif action.action == "read_chunk_detail":
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
                    step_observation = "read_chunk_detail returned no scoped record."
                    tool_payload = {"status": "ok", "found": False}
                else:
                    observed_file_names.add(item.file_name)
                    step_observation = _trim_observation(
                        "read_chunk_detail returned "
                        f"parent_id={item.parent_id} in file={item.file_name}."
                    )
                    tool_payload = {
                        "status": "ok",
                        "found": True,
                        "evidence": _compact_evidence_payload([item])[0],
                    }
            elif action.action == "read_file_chunks":
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
                    "read_file_chunks returned "
                    f"{len(evidence)} parent chunks for file={file_label!r}."
                )
                tool_payload = {
                    "status": "ok",
                    "evidence_count": len(evidence),
                    "evidence": _compact_evidence_payload(evidence),
                }
            elif action.action == "read_skill_reference":
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
                    f"read_skill_reference loaded skill_name={args.skill_name} ref_id={args.ref_id} "
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
            elif action.action == "provide_final_answer":
                args = FinishArguments.model_validate(action.arguments)
                normalized_answer = str(args.answer or "").strip()
                if not normalized_answer:
                    normalized_answer = "No answer found in the provided context."
                if (
                    inventory_baseline_file_names
                    and normalized_answer != "No answer found in the provided context."
                ):
                    baseline_codes = _record_codes_from_file_names(inventory_baseline_file_names)
                    answer_codes = _extract_record_codes(normalized_answer)
                    extra_codes = sorted(answer_codes - baseline_codes)
                    missing_codes = sorted(baseline_codes - answer_codes)
                    if extra_codes or missing_codes:
                        step_observation = _trim_observation(
                            "provide_final_answer failed inventory validation. "
                            f"Extra codes: {', '.join(extra_codes) or 'none'}. "
                            f"Missing codes: {', '.join(missing_codes) or 'none'}."
                        )
                        tool_payload = {
                            "status": "error",
                            "error": step_observation,
                            "inventory_baseline_files": sorted(inventory_baseline_file_names),
                        }
                        _append_tool_message(
                            messages,
                            tool_name="provide_final_answer",
                            payload=tool_payload,
                            tool_call_id=current_tool_call_id,
                        )
                        log_agentic_query_action(
                            run_id=run_id,
                            step=step,
                            action="provide_final_answer_inventory_validation",
                            arguments={
                                "extra_codes": extra_codes,
                                "missing_codes": missing_codes,
                                "inventory_baseline_files": sorted(inventory_baseline_file_names),
                                "intent": trace_intent,
                                "success_criteria": trace_success,
                                "fallback": trace_fallback,
                            },
                            result=tool_payload,
                            error=step_observation,
                        )
                        step_traces.append(
                            {
                                "step": step,
                                "action": "provide_final_answer_inventory_validation",
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
                            status="warning",
                            message=f"Step {step}: rejected final answer outside inventory baseline",
                            metadata={
                                "runId": run_id,
                                "step": step,
                                "action": "provide_final_answer_inventory_validation",
                                "tool": "provide_final_answer",
                                "observation": step_observation,
                                "transcriptMessage": _tool_transcript_item(
                                    action_name="provide_final_answer",
                                    step=step,
                                    observation=step_observation,
                                    status="failed",
                                ),
                            },
                        )
                        continue
                allowed_final_file_names = (
                    inventory_baseline_file_names
                    if inventory_baseline_file_names
                    else observed_file_names
                )
                normalized_citations = _normalize_citations(
                    args.citations,
                    allowed_file_names=allowed_final_file_names,
                )
                if not normalized_citations:
                    normalized_citations = sorted(allowed_final_file_names)
                step_observation = _trim_observation(
                    "provide_final_answer returned final answer with "
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
                    action="provide_final_answer",
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
                    tool_name="provide_final_answer",
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
        if action.action != "provide_final_answer":
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
                    "and return a provide_final_answer JSON object. If evidence does not answer the query, use "
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
            action.action = _canonical_action_name(action.action)
            if action.action == "provide_final_answer":
                forced_tool_call_id = _tool_call_id(run_id, forced_step, "provide_final_answer")
                _attach_tool_call(
                    forced_assistant_message,
                    tool_call_id=forced_tool_call_id,
                    action_name="provide_final_answer",
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
                    tool_name="provide_final_answer",
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
