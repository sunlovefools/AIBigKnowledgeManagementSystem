from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Specifying the txt file names where debug logs will be stored. These files will be created in backend/debug/logs/
_RETRIEVAL_RERANK_FILE = "retrieval_rerank_debug.txt"
_ANSWER_FILE = "answer_generation_debug.txt"
_VECTOR_DB_PARENT_CHUNKS_FILE = "vector_database_parent_chunks_log.txt"
_MOD_AGENT_FILE = "modification_agent_debug.txt"
_AGENTIC_QUERY_FILE = "agentic_query_debug.txt"


def _resolve_debug_dir() -> Path:
    """Resolve backend/debug/logs whether running from project root or backend/."""
    cwd = Path.cwd()
    if (cwd / "backend").is_dir():
        backend_dir = cwd / "backend"
    elif cwd.name == "backend":
        backend_dir = cwd
    else:
        backend_dir = Path(__file__).resolve().parent.parent

    debug_dir = backend_dir / "debug" / "logs" # The directory where debug logs will be stored
    debug_dir.mkdir(parents=True, exist_ok=True)
    return debug_dir


def _append(file_name: str, lines: list[str]) -> None:
    """
    Append lines to a debug log file in backend/debug/logs/, creating the file if it doesn't exist.

    Args:
        file_name: The name of the debug log file (e.g., "retrieval_rerank_debug.txt").
        lines: A list of strings to append to the file. Each string will be written on a new line.
    """
    path = _resolve_debug_dir() / file_name # Resolve the full path to the debug log file
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def log_vector_db_result(function_name: str, retrieved: dict | list, context: dict | None = None) -> None:
    """
    Append vector DB retrieval/debug results into backend/debug/logs/vector_database_parent_chunks_log.txt.

    Args:
        function_name: Name of the function producing the output.
        retrieved: The payload/result object to persist.
        context: Optional metadata/context for the retrieval operation.
    """
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        lines = [
            "=" * 80,
            f"Function: {function_name}",
            f"Timestamp: {timestamp}",
        ]

        if context:
            lines.append("Context:")
            lines.append(json.dumps(context, ensure_ascii=False, indent=2))

        lines.append("Retrieved:")
        lines.append(json.dumps(retrieved, ensure_ascii=False, indent=2))
        _append(_VECTOR_DB_PARENT_CHUNKS_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write vector DB debug log: {exc}")


def _extract_doc_and_score(item: Any) -> tuple[Any, float | None]:
    """
    Extract doc and score from tuple/object forms without raising.
    
    Args:
        item: The input which can be a document object, a tuple of (doc, score)
              or a document object with score in metadata.

    Returns:        
        A tuple of (doc, score) where doc is the document object and score is a float or None if not found.
    """
    doc = item
    score: float | None = None

    if isinstance(item, tuple):
        if len(item) >= 1:
            doc = item[0]
        if len(item) >= 2:
            try:
                score = float(item[1]) if item[1] is not None else None
            except Exception:
                score = None

    metadata = getattr(doc, "metadata", None)
    if score is None and isinstance(metadata, dict):
        for key in ("bge_score", "score"):
            if key in metadata:
                try:
                    score = float(metadata[key])
                    break
                except Exception:
                    score = None

    return doc, score


def _extract_parent_id(doc: Any) -> str:
    """
    Extract parent ID from a document's metadata if available, otherwise return "N/A".

    Args:
        doc: The document object which contain metadata (In the form of a dictionary) which containing parent ID.
    """
    metadata = getattr(doc, "metadata", None)
    if isinstance(metadata, dict):
        parent_id = metadata.get("parent_id") or metadata.get("parent_doc_id")
        if parent_id is not None:
            return str(parent_id)
    return "N/A"


def _extract_content(doc: Any) -> str:
    """
    Extract content from a document object or dict, handling common cases and avoiding errors. 
    If content cannot be found, returns "N/A".

    Args:
        doc: The document object or dictionary which contain the content in "page_content" field or as an attribute.
    """
    content = getattr(doc, "page_content", None)
    if content is not None:
        return str(content)
    if isinstance(doc, dict):
        content = doc.get("page_content")
        if content is not None:
            return str(content)
    return "N/A"


def log_child_chunks(query: str, child_chunks: list, *, top_k: int | None = None) -> None:
    """
    Append retrieval child chunk debug lines in the expected text format.
    
    Args:
        query: The user query string that was used for retrieval.
        child_chunks: A list of retrieved child chunks, which can be in the form of document objects, tuples of (doc, score), or dicts.
        top_k: Optional integer indicating how many top results were requested for retrieval.
    """
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        lines: list[str] = [
            "=" * 50,
            f"DEBUG: Child chunk with query of: {query}",
            f"Timestamp: {timestamp}",
        ]
        if top_k is not None:
            lines.append(f"Top-K Requested: {top_k}")
        lines.append("-" * 50)

        if not child_chunks:
            lines.append("No child chunks found.")

        for index, item in enumerate(child_chunks, start=1):
            doc, score = _extract_doc_and_score(item)
            parent_id = _extract_parent_id(doc)
            content = _extract_content(doc)
            score_str = f"{score:.6f}" if isinstance(score, float) else "N/A"

            lines.append(f"[Child Chunk {index} | Linked to Parent ID: {parent_id} | Score: {score_str}]")
            lines.append(f"Content: {content}")
            lines.append("")

        lines.append("-" * 50)
        _append(_RETRIEVAL_RERANK_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write retrieval debug log: {exc}")


def log_reranker_results(reranked_docs: list, *, top_k: int = 5) -> None:
    """Append reranker results in the expected text format."""
    try:
        lines: list[str] = [
            "-" * 50,
            f"\u2696\ufe0f  RERANKER RESULTS (Top {top_k} Selected)",
            "-" * 50,
        ]

        if not reranked_docs:
            lines.append("No reranker results.")

        for index, item in enumerate(reranked_docs, start=1):
            content, score = _extract_doc_and_score(item)
            score_str = f"{score:.6f}" if isinstance(score, float) else "N/A"

            lines.append(f"[Rank {index} | Reranker Score: {score_str}]")
            lines.append(f"Content: {content}")
            lines.append("")

        lines.append("-" * 50)
        _append(_RETRIEVAL_RERANK_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write reranker debug log: {exc}")


def log_answer_generation_request(provider: str, model: str | None, user_query: str, rag_context: str) -> None:
    """Append answer-generation request block onto backend/debug/logs/answer_generation_request.txt."""
    try:
        timestamp = datetime.now(timezone.utc).isoformat()

        lines = [
            "=" * 50,
            "DEBUG: ANSWER GENERATION REQUEST",
            "-" * 50,
            f"Timestamp: {timestamp}",
            f"Provider: {provider}",
            f"Model: {model if model else 'N/A'}",
            f"Context Length: {len(rag_context)}",
            "",
            "<QUERY>",
            user_query,
            "</QUERY>",
            "",
            "<CONTEXT>",
            rag_context,
            "</CONTEXT>",
            "-" * 50,
        ]
        _append(_ANSWER_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write answer request debug log: {exc}")


def log_answer_generation_response(answer: str) -> None:
    """Append answer-generation response block onto backend/debug/logs/answer_generation_response.txt."""
    try:
        timestamp = datetime.now(timezone.utc).isoformat()

        lines = [
            "-" * 50,
            "DEBUG: ANSWER GENERATION RESPONSE",
            "-" * 50,
            f"Timestamp: {timestamp}",
            f"Answer Length: {len(answer)}",
            "Answer:",
            answer,
            "=" * 50,
        ]
        _append(_ANSWER_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write answer response debug log: {exc}")


def log_modification_agent_llm_request(
    *,
    provider: str,
    model: str | None,
    step: str | None,
    run_id: str | None,
    system_prompt: str,
    user_message: str,
) -> None:
    """Append agentic-LLM request block to backend/debug/logs/modification_agent_debug.txt."""
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        lines = [
            "=" * 50,
            "DEBUG: MODIFICATION AGENT LLM REQUEST",
            "-" * 50,
            f"Timestamp: {timestamp}",
            f"Provider: {provider}",
            f"Model: {model if model else 'N/A'}",
            f"Run ID: {run_id if run_id else 'N/A'}",
            f"Step: {step if step else 'N/A'}",
            f"System Prompt Length: {len(system_prompt)}",
            f"User Message Length: {len(user_message)}",
            "",
            "<SYSTEM_PROMPT>",
            system_prompt,
            "</SYSTEM_PROMPT>",
            "",
            "<USER_MESSAGE>",
            user_message,
            "</USER_MESSAGE>",
            "-" * 50,
        ]
        _append(_MOD_AGENT_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write modification agent request debug log: {exc}")


def log_modification_agent_llm_response(
    *,
    provider: str,
    model: str | None,
    step: str | None,
    run_id: str | None,
    response_text: str,
) -> None:
    """Append agentic-LLM response block to backend/debug/logs/modification_agent_debug.txt."""
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        lines = [
            "-" * 50,
            "DEBUG: MODIFICATION AGENT LLM RESPONSE",
            "-" * 50,
            f"Timestamp: {timestamp}",
            f"Provider: {provider}",
            f"Model: {model if model else 'N/A'}",
            f"Run ID: {run_id if run_id else 'N/A'}",
            f"Step: {step if step else 'N/A'}",
            f"Response Length: {len(response_text)}",
            "<RESPONSE>",
            response_text,
            "</RESPONSE>",
            "=" * 50,
        ]
        _append(_MOD_AGENT_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write modification agent response debug log: {exc}")


def log_modification_agent_search_group(
    *,
    run_id: str | None,
    step: str | None,
    payload: dict[str, Any],
) -> None:
    """Append node-2 search/group payload to backend/debug/logs/modification_agent_debug.txt."""
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        lines = [
            "=" * 50,
            "DEBUG: MODIFICATION AGENT SEARCH GROUP",
            "-" * 50,
            f"Timestamp: {timestamp}",
            f"Run ID: {run_id if run_id else 'N/A'}",
            f"Step: {step if step else 'N/A'}",
            "<SEARCH_GROUP_PAYLOAD>",
            json.dumps(payload, ensure_ascii=False, indent=2),
            "</SEARCH_GROUP_PAYLOAD>",
            "=" * 50,
        ]
        _append(_MOD_AGENT_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write modification agent search/group debug log: {exc}")


def _truncate_for_log(text: str, *, max_chars: int = 12000) -> str:
    normalized = str(text or "")
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars] + f"\n...[truncated {len(normalized) - max_chars} chars]"


def _format_agentic_scalar(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    text = " ".join(str(value).split())
    return text if text else "N/A"


def _format_agentic_mapping(payload: dict[str, Any], *, indent: str = "- ") -> list[str]:
    lines: list[str] = []
    for key, value in payload.items():
        label = str(key).replace("_", " ").title()
        if isinstance(value, dict):
            if not value:
                lines.append(f"{indent}{label}: none")
                continue
            lines.append(f"{indent}{label}:")
            lines.extend(_format_agentic_mapping(value, indent="  " + indent))
        elif isinstance(value, list):
            if not value:
                lines.append(f"{indent}{label}: none")
                continue
            lines.append(f"{indent}{label}:")
            lines.extend(_format_agentic_list(value, indent="  " + indent))
        else:
            lines.append(f"{indent}{label}: {_format_agentic_scalar(value)}")
    return lines


def _format_agentic_evidence_item(item: dict[str, Any], index: int, *, indent: str = "- ") -> list[str]:
    parent_id = item.get("parent_id") or item.get("id") or "N/A"
    file_name = item.get("file_name") or "unknown file"
    chunk_number = item.get("chunk_number")
    snippet = item.get("snippet") or item.get("content") or item.get("page_content") or ""
    structured_view = item.get("structured_view") or ""
    title = f"{indent}{index}. {file_name}"
    details: list[str] = [title]
    details.append(f"{indent}   Parent ID: {_format_agentic_scalar(parent_id)}")
    if chunk_number is not None:
        details.append(f"{indent}   Chunk: {_format_agentic_scalar(chunk_number)}")
    if snippet:
        details.append(f"{indent}   Snippet: {_truncate_for_log(_format_agentic_scalar(snippet), max_chars=700)}")
    if structured_view:
        details.append(f"{indent}   Structured view: {_truncate_for_log(str(structured_view), max_chars=900)}")
    return details


def _format_agentic_list(items: list[Any], *, indent: str = "- ") -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict) and (
            "parent_id" in item
            or "file_name" in item
            or "snippet" in item
            or "structured_view" in item
        ):
            lines.extend(_format_agentic_evidence_item(item, index, indent=indent))
        elif isinstance(item, dict):
            lines.append(f"{indent}{index}.")
            lines.extend(_format_agentic_mapping(item, indent="  " + indent))
        else:
            lines.append(f"{indent}{index}. {_format_agentic_scalar(item)}")
    return lines


def _format_agentic_payload(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict) or not payload:
        return ["- none"]
    return _format_agentic_mapping(payload)


def _format_agentic_action_log(
    *,
    action: str,
    arguments: dict[str, Any] | None,
    result: dict[str, Any] | None,
    error: str | None,
) -> list[str]:
    arguments = arguments if isinstance(arguments, dict) else {}
    result = result if isinstance(result, dict) else {}
    lines: list[str] = []

    intent = arguments.get("intent")
    success_criteria = arguments.get("success_criteria")
    fallback = arguments.get("fallback")
    if intent:
        lines.append(f"Intent: {_format_agentic_scalar(intent)}")
    lines.append(f"Action: {action}")
    if success_criteria:
        lines.append(f"Success Criteria: {_format_agentic_scalar(success_criteria)}")
    if fallback:
        lines.append(f"Fallback / Next Step If Needed: {_format_agentic_scalar(fallback)}")
    if error:
        lines.append(f"Error: {_format_agentic_scalar(error)}")

    tool_arguments = {
        key: value
        for key, value in arguments.items()
        if key not in {"intent", "success_criteria", "fallback", "decision"}
    }
    lines.extend(["", "Arguments:"])
    lines.extend(_format_agentic_payload(tool_arguments))
    lines.extend(["", "Result:"])
    lines.extend(_format_agentic_payload(result))
    return lines


def _format_agentic_config_event(event: str, payload: dict[str, Any] | None) -> list[str]:
    payload = payload if isinstance(payload, dict) else {}
    lines = [f"Event Summary: {event.replace('_', ' ').title()}"]

    if event == "assistant_action_summary":
        lines.extend(
            _format_agentic_action_log(
                action=str(payload.get("action") or "unknown"),
                arguments={
                    **(payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}),
                    "intent": payload.get("intent"),
                    "success_criteria": payload.get("success_criteria"),
                    "fallback": payload.get("fallback"),
                },
                result={},
                error=None,
            )
        )
        return lines

    preferred_keys = [
        "termination_reason",
        "search_scope",
        "included_file_ids_count",
        "skill_bodies_preloaded",
        "loaded_skill_names",
        "references_read_count",
        "references_read_ids",
        "recent_step_trace",
        "skill_name",
        "ref_id",
        "body_length",
        "content_length",
        "cached",
    ]
    summary = {key: payload[key] for key in preferred_keys if key in payload}
    remaining = {key: value for key, value in payload.items() if key not in summary}

    if summary:
        lines.extend(["", "Key Details:"])
        lines.extend(_format_agentic_mapping(summary))
    if remaining:
        lines.extend(["", "Additional Details:"])
        lines.extend(_format_agentic_mapping(remaining))
    if not payload:
        lines.append("- none")
    return lines


def log_agentic_query_llm_request(
    *,
    run_id: str | None,
    step: int | None,
    system_prompt: str,
    user_prompt: str,
) -> None:
    """Append agentic query LLM request details to backend/debug/logs/agentic_query_debug.txt."""
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        lines = [
            "=" * 50,
            "DEBUG: AGENTIC QUERY LLM REQUEST",
            "-" * 50,
            f"Timestamp: {timestamp}",
            f"Run ID: {run_id if run_id else 'N/A'}",
            f"Step: {step if isinstance(step, int) else 'N/A'}",
            f"System Prompt Length: {len(system_prompt)}",
            f"User Prompt Length: {len(user_prompt)}",
            "",
            "<SYSTEM_PROMPT>",
            _truncate_for_log(system_prompt),
            "</SYSTEM_PROMPT>",
            "",
            "<USER_PROMPT>",
            _truncate_for_log(user_prompt),
            "</USER_PROMPT>",
            "-" * 50,
        ]
        _append(_AGENTIC_QUERY_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write agentic query LLM request debug log: {exc}")


def log_agentic_query_llm_response(
    *,
    run_id: str | None,
    step: int | None,
    response_text: str,
) -> None:
    """Append agentic query LLM raw response to backend/debug/logs/agentic_query_debug.txt."""
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        lines = [
            "-" * 50,
            "DEBUG: AGENTIC QUERY LLM RESPONSE",
            "-" * 50,
            f"Timestamp: {timestamp}",
            f"Run ID: {run_id if run_id else 'N/A'}",
            f"Step: {step if isinstance(step, int) else 'N/A'}",
            f"Response Length: {len(response_text)}",
            "<LLM_RESPONSE>",
            _truncate_for_log(response_text),
            "</LLM_RESPONSE>",
            "-" * 50,
        ]
        _append(_AGENTIC_QUERY_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write agentic query LLM response debug log: {exc}")


def log_agentic_query_action(
    *,
    run_id: str | None,
    step: int | None,
    action: str,
    arguments: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Append one agentic query action execution block to backend/debug/logs/agentic_query_debug.txt."""
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        lines = [
            "=" * 50,
            "DEBUG: AGENTIC QUERY ACTION",
            "-" * 50,
            f"Timestamp: {timestamp}",
            f"Run ID: {run_id if run_id else 'N/A'}",
            f"Step: {step if isinstance(step, int) else 'N/A'}",
            "",
            *_format_agentic_action_log(
                action=action,
                arguments=arguments,
                result=result,
                error=str(error) if error else None,
            ),
            "=" * 50,
        ]
        _append(_AGENTIC_QUERY_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write agentic query action debug log: {exc}")


def log_agentic_query_config_event(
    *,
    run_id: str | None,
    event: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append agentic query config/runtime transparency events to backend/debug/logs/agentic_query_debug.txt."""
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        lines = [
            "=" * 50,
            "DEBUG: AGENTIC QUERY CONFIG EVENT",
            "-" * 50,
            f"Timestamp: {timestamp}",
            f"Run ID: {run_id if run_id else 'N/A'}",
            f"Event: {event}",
            "",
            *_format_agentic_config_event(event, payload),
            "=" * 50,
        ]
        _append(_AGENTIC_QUERY_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write agentic query config event debug log: {exc}")


_TOKEN_USAGE_FILE = "token_usage.txt"
_TOKEN_USAGE_SUMMARY_FILE = "token_usage_summary.txt"


def _update_token_summary(
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    estimated_cost_usd: float,
) -> None:
    """
    Rewrite token_usage_summary.txt with updated cumulative stats and averages.

    Reads the existing summary to get previous totals, adds the new call's data,
    then overwrites the file with fresh aggregated numbers.

    Summary file format:
        Total Queries, Total Prompt Tokens, Total Completion Tokens,
        Total Tokens, Total Cost — all as plain integers/floats on separate lines,
        followed by a human-readable display block.
    """
    debug_dir = _resolve_debug_dir()
    summary_path = debug_dir / _TOKEN_USAGE_SUMMARY_FILE

    # --- Read existing totals (if file exists) ---
    prev_queries = 0
    prev_prompt = 0
    prev_completion = 0
    prev_total = 0
    prev_cost = 0.0

    if summary_path.exists():
        try:
            content = summary_path.read_text(encoding="utf-8")
            for line in content.splitlines():
                if line.startswith("TOTAL_QUERIES:"):
                    prev_queries = int(line.split(":")[1].strip())
                elif line.startswith("TOTAL_PROMPT_TOKENS:"):
                    prev_prompt = int(line.split(":")[1].strip())
                elif line.startswith("TOTAL_COMPLETION_TOKENS:"):
                    prev_completion = int(line.split(":")[1].strip())
                elif line.startswith("TOTAL_TOKENS:"):
                    prev_total = int(line.split(":")[1].strip())
                elif line.startswith("TOTAL_COST_USD:"):
                    prev_cost = float(line.split(":")[1].strip())
        except Exception:
            pass  # If parsing fails, start fresh

    # --- Compute new cumulative totals ---
    new_queries = prev_queries + 1
    new_prompt = prev_prompt + prompt_tokens
    new_completion = prev_completion + completion_tokens
    new_total = prev_total + total_tokens
    new_cost = prev_cost + estimated_cost_usd

    avg_prompt = new_prompt / new_queries
    avg_completion = new_completion / new_queries
    avg_total = new_total / new_queries
    avg_cost = new_cost / new_queries

    timestamp = datetime.now(timezone.utc).isoformat()

    # --- Overwrite the summary file ---
    lines = [
        # Machine-readable lines (used for parsing on next update)
        f"TOTAL_QUERIES:            {new_queries}",
        f"TOTAL_PROMPT_TOKENS:      {new_prompt}",
        f"TOTAL_COMPLETION_TOKENS:  {new_completion}",
        f"TOTAL_TOKENS:             {new_total}",
        f"TOTAL_COST_USD:           {new_cost:.6f}",
        "",
        # Human-readable display block
        "=" * 50,
        "TOKEN USAGE SUMMARY",
        "-" * 50,
        f"Last Updated:             {timestamp}",
        "-" * 50,
        f"Total Queries:            {new_queries}",
        f"Total Prompt Tokens:      {new_prompt:,}",
        f"Total Completion Tokens:  {new_completion:,}",
        f"Total Tokens Used:        {new_total:,}",
        f"Total Est. Cost (USD):    ${new_cost:.6f}",
        "-" * 50,
        f"Avg Prompt Tokens/Query:      {avg_prompt:,.1f}",
        f"Avg Completion Tokens/Query:  {avg_completion:,.1f}",
        f"Avg Total Tokens/Query:       {avg_total:,.1f}",
        f"Avg Est. Cost/Query (USD):    ${avg_cost:.6f}",
        "=" * 50,
    ]

    try:
        summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"Warning: failed to update token usage summary: {exc}")


def log_token_usage(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    estimated_cost_usd: float,
    operation: str | None = None,
    run_id: str | None = None,
    step: str | None = None,
) -> None:
    """
    Append token usage stats for a single LLM call to backend/debug/logs/token_usage.txt,
    and update the cumulative summary in token_usage_summary.txt.

    Token data is NOT returned to the frontend — logged here and printed to terminal only.

    Args:
        provider: LLM provider name (e.g. "OPENROUTER", "BEAM").
        model: Model name (e.g. "deepseek-chat").
        prompt_tokens: Number of input tokens sent to the model.
        completion_tokens: Number of output tokens generated by the model.
        total_tokens: Total tokens consumed (prompt + completion).
        estimated_cost_usd: Estimated USD cost for this call.
        operation: Optional operation name (e.g. "modification_agent_run").
        run_id: Optional request/run correlation ID.
        step: Optional sub-step name within an operation.
    """
    try:
        timestamp = datetime.now(timezone.utc).isoformat()

        lines = [
            "=" * 50,
            "TOKEN USAGE",
            "-" * 50,
            f"Timestamp:         {timestamp}",
            f"Provider:          {provider}",
            f"Model:             {model}",
            f"Operation:         {operation or 'N/A'}",
            f"Run ID:            {run_id or 'N/A'}",
            f"Step:              {step or 'N/A'}",
            f"Prompt Tokens:     {prompt_tokens}",
            f"Completion Tokens: {completion_tokens}",
            f"Total Tokens:      {total_tokens}",
            f"Est. Cost (USD):   ${estimated_cost_usd:.6f}",
            "=" * 50,
        ]
        _append(_TOKEN_USAGE_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write token usage log: {exc}")

    # Only update summary for queries that actually consumed tokens
    # (skips failed/empty responses from rate-limited free models)
    if total_tokens > 0:
        _update_token_summary(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost_usd,
        )
