"""Node 6: edit confirmed parent chunks into frontend-ready proposals."""
from __future__ import annotations

import asyncio
from typing import Any

from ..prompts.retrieval_brief_prompts import (
    EDITOR_NODE_SYSTEM_PROMPT,
    EDITOR_NODE_USER_PROMPT,
)
from ..services import llm_client, vector_search
from ..shared.logging import log_modification_agent_search_group
from ..shared.normalization import _normalize_goal
from ..state.retrieval_brief_state import RetrievalBriefState

_EDITOR_MAX_CONCURRENCY = 5
_UNSAFE_PREFIXES = (
    "i'm sorry",
    "i am sorry",
    "i cannot",
    "as an ai",
    "cannot safely",
)


def _normalize_confirmed_parent_chunk_refs(raw_refs: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_refs, list):
        return []

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw_ref in raw_refs:
        if not isinstance(raw_ref, dict):
            continue
        file_id = str(raw_ref.get("file_id") or "").strip()
        file_name = str(raw_ref.get("file_name") or "").strip() or "unknown"
        parent_chunk_number = raw_ref.get("parent_chunk_number")
        if not file_id or not isinstance(parent_chunk_number, int):
            continue

        dedupe_key = f"{file_id}::{parent_chunk_number}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(
            {
                "file_id": file_id,
                "file_name": file_name,
                "parent_chunk_number": parent_chunk_number,
            }
        )

    normalized.sort(
        key=lambda item: (
            str(item.get("file_name") or ""),
            str(item.get("file_id") or ""),
            int(item.get("parent_chunk_number", 10**9)),
        )
    )
    return normalized


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned

    lines = cleaned.splitlines()
    if not lines:
        return ""

    if lines[-1].strip().startswith("```"):
        body = lines[1:-1]
    else:
        body = lines[1:]
    return "\n".join(body).strip()


def _normalize_edited_text(raw_text: str, original_text: str) -> str:
    cleaned = _strip_code_fence(str(raw_text or ""))
    if not cleaned:
        return original_text

    lowered = cleaned.casefold()
    for prefix in ("output:", "edited text:", "edited_text:"):
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            lowered = cleaned.casefold()
            break

    if not cleaned:
        return original_text

    if any(lowered.startswith(prefix) for prefix in _UNSAFE_PREFIXES):
        return original_text

    return cleaned


async def _edit_one_parent_chunk(
    state: RetrievalBriefState,
    *,
    goal: str,
    parent_chunk: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int], int, dict[str, Any] | None]:
    original_text = str(parent_chunk.get("page_content") or "")
    file_id = str(parent_chunk.get("file_id") or "").strip()
    file_name = str(parent_chunk.get("file_name") or "").strip() or "unknown"
    parent_id = str(parent_chunk.get("parent_id") or "").strip()
    parent_chunk_number = parent_chunk.get("chunk_number")
    usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }

    if not file_id or not parent_id or not isinstance(parent_chunk_number, int):
        return (
            {
                "file_id": file_id or "unknown",
                "file_name": file_name,
                "parent_id": parent_id or "unknown",
                "parent_chunk_number": parent_chunk_number,
                "outcome": "skipped_invalid_parent_chunk",
                "reason": "Missing required parent chunk identifiers.",
            },
            usage,
            0,
            None,
        )

    try:
        llm_text, llm_usage = await llm_client._call_llm(
            system_prompt=EDITOR_NODE_SYSTEM_PROMPT,
            user_message=EDITOR_NODE_USER_PROMPT.format(
                goal=goal,
                text=original_text,
            ),
            session=state.get("_session"),
            run_id=state.get("run_id"),
            step="editor_node",
            max_tokens=2048,
        )
        usage = {
            "prompt_tokens": int(llm_usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(llm_usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(llm_usage.get("total_tokens", 0) or 0),
        }
    except Exception as error:
        return (
            {
                "file_id": file_id,
                "file_name": file_name,
                "parent_id": parent_id,
                "parent_chunk_number": parent_chunk_number,
                "outcome": "llm_failed_fallback_original",
                "reason": str(error),
            },
            usage,
            0,
            None,
        )

    edited_text = _normalize_edited_text(llm_text, original_text)
    if edited_text.strip() == original_text.strip():
        return (
            {
                "file_id": file_id,
                "file_name": file_name,
                "parent_id": parent_id,
                "parent_chunk_number": parent_chunk_number,
                "outcome": "unchanged_skipped",
                "reason": "No meaningful content change detected.",
            },
            usage,
            1,
            None,
        )

    proposal = {
        "fileId": file_id,
        "fileName": file_name,
        "parentId": parent_id,
        "original": original_text,
        "proposed": edited_text,
        "source": "agent",
    }
    return (
        {
            "file_id": file_id,
            "file_name": file_name,
            "parent_id": parent_id,
            "parent_chunk_number": parent_chunk_number,
            "outcome": "edited",
            "reason": "Edited parent chunk generated.",
        },
        usage,
        1,
        proposal,
    )


async def run_editor_batch(
    state: RetrievalBriefState,
    *,
    parent_chunk_constraint_verifier_result: dict[str, Any],
    batch_id: int | None = None,
) -> tuple[dict[str, Any], dict[str, int], int, list[dict[str, Any]]]:
    goal = _normalize_goal(state.get("goal"), state.get("user_instructions", ""))
    resolved_batch_id = int(batch_id or 1)
    retrieval_cache = vector_search._ensure_retrieval_cache(state.get("_retrieval_cache"))
    state["_retrieval_cache"] = retrieval_cache

    usage_totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    llm_calls_made = 0

    raw_refs = (
        parent_chunk_constraint_verifier_result.get("merged_confirmed_parent_chunk_refs", [])
        if isinstance(parent_chunk_constraint_verifier_result, dict)
        else []
    )
    confirmed_refs = _normalize_confirmed_parent_chunk_refs(raw_refs)

    if not confirmed_refs:
        node_result = {
            "batch_id": resolved_batch_id,
            "goal": goal,
            "edits": [],
            "proposals": [],
            "errors": "none",
            "run_summary": {
                "confirmed_parent_chunk_ref_count": 0,
                "resolved_parent_chunk_count": 0,
                "edited_parent_chunk_count": 0,
                "unchanged_parent_chunk_count": 0,
                "llm_call_count": 0,
                "error_count": 0,
                "cache_stats": vector_search._snapshot_retrieval_cache_stats(retrieval_cache),
            },
        }
        return node_result, usage_totals, llm_calls_made, []

    requested_by_file: dict[str, dict[str, Any]] = {}
    for ref in confirmed_refs:
        file_id = ref["file_id"]
        file_entry = requested_by_file.get(file_id)
        if not isinstance(file_entry, dict):
            file_entry = {
                "file_name": ref["file_name"],
                "chunk_numbers": [],
            }
            requested_by_file[file_id] = file_entry
        if ref["parent_chunk_number"] not in file_entry["chunk_numbers"]:
            file_entry["chunk_numbers"].append(ref["parent_chunk_number"])

    fetch_results = await asyncio.gather(
        *[
            vector_search._fetch_parent_chunks_for_file_chunk_numbers(
                file_id=file_id,
                chunk_numbers=file_entry["chunk_numbers"],
                cache=retrieval_cache,
            )
            for file_id, file_entry in requested_by_file.items()
        ],
        return_exceptions=True,
    )

    resolved_parent_chunks: list[dict[str, Any]] = []
    edit_errors: list[dict[str, Any]] = []

    for (file_id, file_entry), fetch_result in zip(requested_by_file.items(), fetch_results):
        file_name = str(file_entry.get("file_name") or "unknown")
        if isinstance(fetch_result, Exception):
            edit_errors.append(
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "error": str(fetch_result),
                    "stage": "fetch_parent_chunks",
                }
            )
            continue

        resolved_map = fetch_result if isinstance(fetch_result, dict) else {}
        for chunk_number in file_entry["chunk_numbers"]:
            payload = resolved_map.get(chunk_number)
            if not isinstance(payload, dict):
                edit_errors.append(
                    {
                        "file_id": file_id,
                        "file_name": file_name,
                        "parent_chunk_number": chunk_number,
                        "error": "Parent chunk is unavailable for confirmed reference.",
                        "stage": "resolve_parent_chunk",
                    }
                )
                continue
            payload_file_name = str(payload.get("file_name") or "").strip() or file_name
            resolved_parent_chunks.append(
                {
                    "file_id": str(payload.get("file_id") or file_id).strip(),
                    "file_name": payload_file_name,
                    "parent_id": str(payload.get("parent_id") or "").strip(),
                    "chunk_number": payload.get("chunk_number"),
                    "page_content": str(payload.get("page_content") or ""),
                }
            )

    resolved_parent_chunks.sort(
        key=lambda item: (
            str(item.get("file_name") or ""),
            str(item.get("file_id") or ""),
            (
                item.get("chunk_number")
                if isinstance(item.get("chunk_number"), int)
                else 10**9
            ),
        )
    )

    semaphore = asyncio.Semaphore(_EDITOR_MAX_CONCURRENCY)

    async def _run_edit_with_limit(
        parent_chunk: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, int], int, dict[str, Any] | None]:
        async with semaphore:
            return await _edit_one_parent_chunk(
                state,
                goal=goal,
                parent_chunk=parent_chunk,
            )

    edit_results_raw = await asyncio.gather(
        *[_run_edit_with_limit(parent_chunk) for parent_chunk in resolved_parent_chunks],
        return_exceptions=True,
    )

    edits: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []

    for parent_chunk, edit_result in zip(resolved_parent_chunks, edit_results_raw):
        if isinstance(edit_result, Exception):
            edit_errors.append(
                {
                    "file_id": parent_chunk.get("file_id"),
                    "file_name": parent_chunk.get("file_name"),
                    "parent_chunk_number": parent_chunk.get("chunk_number"),
                    "error": str(edit_result),
                    "stage": "editor_call",
                }
            )
            edits.append(
                {
                    "file_id": parent_chunk.get("file_id"),
                    "file_name": parent_chunk.get("file_name"),
                    "parent_id": parent_chunk.get("parent_id"),
                    "parent_chunk_number": parent_chunk.get("chunk_number"),
                    "outcome": "llm_failed_fallback_original",
                    "reason": str(edit_result),
                }
            )
            continue

        edit_record, usage, llm_calls, proposal = edit_result
        edits.append(edit_record)
        usage_totals["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
        usage_totals["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
        usage_totals["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
        llm_calls_made += int(llm_calls or 0)
        if isinstance(proposal, dict):
            proposals.append(proposal)

    node_result = {
        "batch_id": resolved_batch_id,
        "goal": goal,
        "edits": edits,
        "proposals": proposals,
        "errors": edit_errors if edit_errors else "none",
        "run_summary": {
            "confirmed_parent_chunk_ref_count": len(confirmed_refs),
            "resolved_parent_chunk_count": len(resolved_parent_chunks),
            "edited_parent_chunk_count": len(proposals),
            "unchanged_parent_chunk_count": sum(
                1
                for item in edits
                if str(item.get("outcome") or "").strip().lower() == "unchanged_skipped"
            ),
            "llm_call_count": llm_calls_made,
            "error_count": len(edit_errors),
            "cache_stats": vector_search._snapshot_retrieval_cache_stats(retrieval_cache),
        },
    }
    return node_result, usage_totals, llm_calls_made, proposals


async def editor_node(state: RetrievalBriefState) -> dict:
    """Node 6 wrapper: edit confirmed parent chunks and emit proposals."""
    print("[Agentic Modification - Node 6] Editing confirmed parent chunks...")
    run_id = state.get("run_id")
    node5_result = state.get("node5_parent_chunk_constraint_verifier_result", {})
    batch_id = 1
    if isinstance(node5_result, dict) and isinstance(node5_result.get("batch_id"), int):
        batch_id = int(node5_result.get("batch_id", 1))

    retrieval_cache = vector_search._ensure_retrieval_cache(state.get("_retrieval_cache"))
    state["_retrieval_cache"] = retrieval_cache

    try:
        node_result, usage_totals, llm_calls_made, proposals = await run_editor_batch(
            state,
            parent_chunk_constraint_verifier_result=node5_result if isinstance(node5_result, dict) else {},
            batch_id=batch_id,
        )
        log_modification_agent_search_group(
            run_id=run_id,
            step="editor_node",
            payload=node_result,
        )
        return {
            "intention": "edit",
            "proposals": proposals,
            "node6_editor_result": node_result,
            "token_prompt_total": int(state.get("token_prompt_total", 0) or 0) + usage_totals["prompt_tokens"],
            "token_completion_total": int(state.get("token_completion_total", 0) or 0)
            + usage_totals["completion_tokens"],
            "token_total": int(state.get("token_total", 0) or 0) + usage_totals["total_tokens"],
            "llm_call_count": int(state.get("llm_call_count", 0) or 0) + llm_calls_made,
            "_retrieval_cache": retrieval_cache,
        }
    except Exception as error:
        error_message = f"editor_node failed: {error}"
        print(error_message)
        log_modification_agent_search_group(
            run_id=run_id,
            step="editor_node",
            payload={"error": error_message},
        )
        return {
            "intention": "edit",
            "proposals": [],
            "node6_editor_result": {
                "batch_id": batch_id,
                "goal": _normalize_goal(state.get("goal"), state.get("user_instructions", "")),
                "edits": [],
                "proposals": [],
                "errors": [{"stage": "editor_node", "error": error_message}],
                "run_summary": {
                    "confirmed_parent_chunk_ref_count": 0,
                    "resolved_parent_chunk_count": 0,
                    "edited_parent_chunk_count": 0,
                    "unchanged_parent_chunk_count": 0,
                    "llm_call_count": 0,
                    "error_count": 1,
                    "cache_stats": vector_search._snapshot_retrieval_cache_stats(retrieval_cache),
                },
            },
        }
