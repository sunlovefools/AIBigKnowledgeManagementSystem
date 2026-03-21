"""Node 5: verify candidate parent chunks against constraint scope."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..prompts.retrieval_brief_prompts import (
    PARENT_CHUNK_CONSTRAINT_VERIFIER_SYSTEM_PROMPT,
    PARENT_CHUNK_CONSTRAINT_VERIFIER_USER_PROMPT,
)
from ..services import llm_client, vector_search
from ..shared.logging import log_modification_agent_search_group
from ..shared.normalization import (
    _normalize_constraint,
    _normalize_goal,
    _parse_json_object,
)
from ..shared.search_utils import _build_parent_chunks_prompt_payload
from ..state.retrieval_brief_state import RetrievalBriefState


def _normalize_chunk_numbers(raw_values: Any) -> list[int]:
    if not isinstance(raw_values, list):
        return []

    normalized: list[int] = []
    seen: set[int] = set()
    for raw_value in raw_values:
        if isinstance(raw_value, bool):
            continue
        if isinstance(raw_value, int):
            chunk_number = raw_value
        elif isinstance(raw_value, float):
            chunk_number = int(raw_value)
        elif isinstance(raw_value, str):
            value = raw_value.strip()
            if not value:
                continue
            try:
                chunk_number = int(value)
            except ValueError:
                continue
        else:
            continue
        if chunk_number in seen:
            continue
        seen.add(chunk_number)
        normalized.append(chunk_number)
    return normalized


def _normalize_boolean(raw_value: Any) -> bool | None:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, int):
        return bool(raw_value)
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


def _normalize_tool_request(raw_parsed: dict[str, Any]) -> dict[str, Any] | None:
    action = str(raw_parsed.get("action") or "").strip().lower()
    if action != "tool":
        return None

    tool_name = str(raw_parsed.get("tool_name") or "").strip()
    arguments = raw_parsed.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}

    if tool_name == "get_parent_chunks":
        start_chunk_number = _normalize_chunk_numbers([arguments.get("start_chunk_number")])
        end_chunk_number = _normalize_chunk_numbers([arguments.get("end_chunk_number")])
        if not start_chunk_number or not end_chunk_number:
            return None
        return {
            "tool_name": tool_name,
            "arguments": {
                "start_chunk_number": start_chunk_number[0],
                "end_chunk_number": end_chunk_number[0],
            },
        }

    if tool_name == "get_surrounding_parent_chunks":
        chunk_number = _normalize_chunk_numbers([arguments.get("chunk_number")])
        if not chunk_number:
            return None
        return {
            "tool_name": tool_name,
            "arguments": {
                "chunk_number": chunk_number[0],
            },
        }

    return None


async def _run_one_parent_chunk_constraint_verification(
    state: RetrievalBriefState,
    *,
    goal: str,
    constraint: str,
    file_id: str,
    file_name: str,
    candidate_parent_chunk_number: int,
    retrieval_cache: dict[str, Any],
    max_tool_rounds: int = 6,
) -> tuple[dict[str, Any], dict[str, int], int]:
    usage_totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    llm_calls_made = 0
    run_id = state.get("run_id")
    session = state.get("_session")

    known_parent_chunks_by_number: dict[int, dict[str, Any]] = {}
    tool_history: list[dict[str, Any]] = []

    origin_parent_chunks = await vector_search._get_parent_chunks_for_file_range(
        file_id=file_id,
        start_chunk_number=candidate_parent_chunk_number,
        end_chunk_number=candidate_parent_chunk_number,
        cache=retrieval_cache,
    )
    if isinstance(origin_parent_chunks, list):
        for parent_chunk in origin_parent_chunks:
            if not isinstance(parent_chunk, dict):
                continue
            chunk_number = parent_chunk.get("chunk_number")
            if isinstance(chunk_number, int):
                known_parent_chunks_by_number[chunk_number] = parent_chunk

    if candidate_parent_chunk_number not in known_parent_chunks_by_number:
        return (
            {
                "file_id": file_id,
                "file_name": file_name,
                "candidate_parent_chunk_number": candidate_parent_chunk_number,
                "candidate_chunk_found": False,
                "is_confirmed": False,
                "reasoning_summary": "Candidate parent chunk is unavailable for this file.",
                "llm_round_count": 0,
                "tool_call_count": 0,
                "tool_history": [],
            },
            usage_totals,
            llm_calls_made,
        )

    last_reasoning = "No final constraint verification decision produced."
    for round_index in range(1, max_tool_rounds + 1):
        parent_chunks_payload = _build_parent_chunks_prompt_payload(
            [
                known_parent_chunks_by_number[number]
                for number in sorted(known_parent_chunks_by_number.keys())
                if isinstance(known_parent_chunks_by_number.get(number), dict)
            ]
        )
        tool_history_payload = json.dumps(tool_history, ensure_ascii=False) if tool_history else "[]"

        llm_text, usage = await llm_client._call_llm(
            messages=[
                {"role": "system", "content": PARENT_CHUNK_CONSTRAINT_VERIFIER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": PARENT_CHUNK_CONSTRAINT_VERIFIER_USER_PROMPT.format(
                        goal=goal,
                        constraint=constraint,
                        candidate_chunk_number=candidate_parent_chunk_number,
                        parent_chunks=parent_chunks_payload,
                    ),
                },
                {
                    "role": "user",
                    "content": f"Tool history (JSON):\n{tool_history_payload}",
                },
            ],
            session=session,
            run_id=run_id,
            step="parent_chunk_constraint_verifier",
            max_tokens=512,
        )
        llm_calls_made += 1
        usage_totals["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
        usage_totals["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
        usage_totals["total_tokens"] += int(usage.get("total_tokens", 0) or 0)

        try:
            raw_parsed = _parse_json_object(llm_text)
        except Exception:
            last_reasoning = "Model output was not valid JSON."
            break

        if "is_confirmed" in raw_parsed or "reasoning_summary" in raw_parsed:
            normalized_confirmation = _normalize_boolean(raw_parsed.get("is_confirmed"))
            is_confirmed = bool(normalized_confirmation) if normalized_confirmation is not None else False
            reasoning_summary = str(raw_parsed.get("reasoning_summary") or "").strip()
            if not reasoning_summary:
                reasoning_summary = (
                    "Constraint clearly applies to the candidate parent chunk."
                    if is_confirmed
                    else "Constraint does not clearly apply to the candidate parent chunk."
                )
            return (
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "candidate_parent_chunk_number": candidate_parent_chunk_number,
                    "candidate_chunk_found": True,
                    "is_confirmed": is_confirmed,
                    "reasoning_summary": reasoning_summary,
                    "llm_round_count": round_index,
                    "tool_call_count": len(tool_history),
                    "tool_history": tool_history,
                },
                usage_totals,
                llm_calls_made,
            )

        tool_request = _normalize_tool_request(raw_parsed)
        if not isinstance(tool_request, dict):
            last_reasoning = "Model output did not match verifier schema."
            break

        tool_name = tool_request["tool_name"]
        arguments = tool_request["arguments"]
        if tool_name == "get_parent_chunks":
            tool_result = await vector_search._get_parent_chunks_for_file_range(
                file_id=file_id,
                start_chunk_number=arguments["start_chunk_number"],
                end_chunk_number=arguments["end_chunk_number"],
                cache=retrieval_cache,
            )
        elif tool_name == "get_surrounding_parent_chunks":
            tool_result = await vector_search._get_surrounding_parent_chunks_for_file(
                file_id=file_id,
                chunk_number=arguments["chunk_number"],
                cache=retrieval_cache,
            )
        else:
            tool_result = None

        result_chunk_numbers: list[int] = []
        if isinstance(tool_result, list):
            for parent_chunk in tool_result:
                if not isinstance(parent_chunk, dict):
                    continue
                chunk_number = parent_chunk.get("chunk_number")
                if not isinstance(chunk_number, int):
                    continue
                result_chunk_numbers.append(chunk_number)
                known_parent_chunks_by_number[chunk_number] = parent_chunk

        tool_history.append(
            {
                "round_index": round_index,
                "tool_name": tool_name,
                "arguments": arguments,
                "result_chunk_numbers": result_chunk_numbers,
                "result": (
                    "null"
                    if not isinstance(tool_result, list)
                    else f"returned_{len(result_chunk_numbers)}_chunks"
                ),
            }
        )

    return (
        {
            "file_id": file_id,
            "file_name": file_name,
            "candidate_parent_chunk_number": candidate_parent_chunk_number,
            "candidate_chunk_found": True,
            "is_confirmed": False,
            "reasoning_summary": last_reasoning,
            "llm_round_count": llm_calls_made,
            "tool_call_count": len(tool_history),
            "tool_history": tool_history,
        },
        usage_totals,
        llm_calls_made,
    )


async def run_parent_chunk_constraint_verifier_batch(
    state: RetrievalBriefState,
    *,
    file_filtering_result: dict[str, Any],
    batch_id: int | None = None,
) -> tuple[dict[str, Any], dict[str, int], int]:
    goal = _normalize_goal(state.get("goal"), state.get("user_instructions", ""))
    constraint = _normalize_constraint(state.get("constraint"))
    resolved_batch_id = int(batch_id or 1)
    retrieval_cache = vector_search._ensure_retrieval_cache(state.get("_retrieval_cache"))
    state["_retrieval_cache"] = retrieval_cache

    usage_totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    llm_calls_made = 0

    evaluations_raw = file_filtering_result.get("evaluations") if isinstance(file_filtering_result, dict) else []
    evaluations = evaluations_raw if isinstance(evaluations_raw, list) else []

    verification_inputs: list[dict[str, Any]] = []
    seen_input_keys: set[str] = set()
    for evaluation in evaluations:
        if not isinstance(evaluation, dict):
            continue
        file_id = str(evaluation.get("file_id") or "").strip()
        file_name = str(evaluation.get("file_name") or "").strip() or "unknown"
        if not file_id:
            continue
        candidate_numbers = _normalize_chunk_numbers(evaluation.get("potential_parent_chunks"))
        for candidate_chunk_number in candidate_numbers:
            input_key = f"{file_id}::{candidate_chunk_number}"
            if input_key in seen_input_keys:
                continue
            seen_input_keys.add(input_key)
            verification_inputs.append(
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "candidate_parent_chunk_number": candidate_chunk_number,
                }
            )

    if not verification_inputs:
        node_result = {
            "batch_id": resolved_batch_id,
            "goal": goal,
            "constraint": constraint,
            "verifications": [],
            "confirmed_parent_chunks_by_file": [],
            "merged_confirmed_parent_chunk_refs": [],
            "run_summary": {
                "verification_count": 0,
                "confirmed_verification_count": 0,
                "rejected_verification_count": 0,
                "confirmed_file_count": 0,
                "confirmed_parent_chunk_ref_count": 0,
                "tool_call_count": 0,
                "llm_call_count": 0,
                "cache_stats": vector_search._snapshot_retrieval_cache_stats(retrieval_cache),
            },
        }
        return node_result, usage_totals, llm_calls_made

    verification_results_raw = await asyncio.gather(
        *[
            _run_one_parent_chunk_constraint_verification(
                state,
                goal=goal,
                constraint=constraint,
                file_id=item["file_id"],
                file_name=item["file_name"],
                candidate_parent_chunk_number=item["candidate_parent_chunk_number"],
                retrieval_cache=retrieval_cache,
            )
            for item in verification_inputs
        ],
        return_exceptions=True,
    )

    verifications: list[dict[str, Any]] = []
    verification_errors: list[dict[str, Any]] = []
    for input_item, raw_result in zip(verification_inputs, verification_results_raw):
        if isinstance(raw_result, Exception):
            verification_errors.append(
                {
                    "file_id": input_item["file_id"],
                    "file_name": input_item["file_name"],
                    "candidate_parent_chunk_number": input_item["candidate_parent_chunk_number"],
                    "error": str(raw_result),
                }
            )
            verifications.append(
                {
                    "file_id": input_item["file_id"],
                    "file_name": input_item["file_name"],
                    "candidate_parent_chunk_number": input_item["candidate_parent_chunk_number"],
                    "candidate_chunk_found": True,
                    "is_confirmed": False,
                    "reasoning_summary": f"Constraint verification failed: {raw_result}",
                    "llm_round_count": 0,
                    "tool_call_count": 0,
                    "tool_history": [],
                }
            )
            continue

        verification_result, usage, llm_calls = raw_result
        usage_totals["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
        usage_totals["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
        usage_totals["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
        llm_calls_made += int(llm_calls or 0)
        verifications.append(verification_result)

    confirmed_by_file_key: dict[str, dict[str, Any]] = {}
    merged_ref_by_key: dict[str, dict[str, Any]] = {}
    for verification in verifications:
        if not isinstance(verification, dict):
            continue
        if not bool(verification.get("is_confirmed", False)):
            continue

        file_id = str(verification.get("file_id") or "").strip()
        file_name = str(verification.get("file_name") or "").strip() or "unknown"
        candidate_chunk_number = verification.get("candidate_parent_chunk_number")
        if not file_id or not isinstance(candidate_chunk_number, int):
            continue

        file_key = f"{file_id}::{file_name}"
        file_entry = confirmed_by_file_key.get(file_key)
        if not isinstance(file_entry, dict):
            file_entry = {
                "file_id": file_id,
                "file_name": file_name,
                "confirmed_parent_chunk_numbers": [],
            }
            confirmed_by_file_key[file_key] = file_entry

        existing_numbers = set(_normalize_chunk_numbers(file_entry.get("confirmed_parent_chunk_numbers")))
        if candidate_chunk_number not in existing_numbers:
            file_entry["confirmed_parent_chunk_numbers"].append(candidate_chunk_number)
            existing_numbers.add(candidate_chunk_number)

        merged_ref_by_key.setdefault(
            f"{file_id}::{candidate_chunk_number}",
            {
                "file_id": file_id,
                "file_name": file_name,
                "parent_chunk_number": candidate_chunk_number,
            },
        )

    confirmed_parent_chunks_by_file = list(confirmed_by_file_key.values())
    for entry in confirmed_parent_chunks_by_file:
        entry["confirmed_parent_chunk_numbers"] = sorted(
            _normalize_chunk_numbers(entry.get("confirmed_parent_chunk_numbers"))
        )
    confirmed_parent_chunks_by_file.sort(
        key=lambda item: (
            str(item.get("file_name") or ""),
            str(item.get("file_id") or ""),
        )
    )

    merged_confirmed_parent_chunk_refs = list(merged_ref_by_key.values())
    merged_confirmed_parent_chunk_refs.sort(
        key=lambda item: (
            str(item.get("file_name") or ""),
            str(item.get("file_id") or ""),
            int(item.get("parent_chunk_number", 10**9)),
        )
    )

    node_result = {
        "batch_id": resolved_batch_id,
        "goal": goal,
        "constraint": constraint,
        "verifications": verifications,
        "confirmed_parent_chunks_by_file": confirmed_parent_chunks_by_file,
        "merged_confirmed_parent_chunk_refs": merged_confirmed_parent_chunk_refs,
        "errors": verification_errors if verification_errors else "none",
        "run_summary": {
            "verification_count": len(verifications),
            "confirmed_verification_count": sum(
                1 for item in verifications if isinstance(item, dict) and bool(item.get("is_confirmed", False))
            ),
            "rejected_verification_count": sum(
                1 for item in verifications if isinstance(item, dict) and not bool(item.get("is_confirmed", False))
            ),
            "confirmed_file_count": len(confirmed_parent_chunks_by_file),
            "confirmed_parent_chunk_ref_count": len(merged_confirmed_parent_chunk_refs),
            "tool_call_count": sum(
                int(item.get("tool_call_count", 0) or 0)
                for item in verifications
                if isinstance(item, dict)
            ),
            "llm_call_count": llm_calls_made,
            "cache_stats": vector_search._snapshot_retrieval_cache_stats(retrieval_cache),
        },
    }
    return node_result, usage_totals, llm_calls_made


async def parent_chunk_constraint_verifier_node(state: RetrievalBriefState) -> dict:
    """Node 5 wrapper: run one parent-chunk constraint verifier batch."""
    print("[Agentic Modification - Node 5] Verifying parent-chunk constraints...")
    run_id = state.get("run_id")
    node4_result = state.get("node4_file_filtering_result", {})
    latest_node4_batch = (
        node4_result.get("latest_batch", {})
        if isinstance(node4_result, dict) and isinstance(node4_result.get("latest_batch"), dict)
        else node4_result
    )
    batch_id = int(latest_node4_batch.get("batch_id", 1)) if isinstance(latest_node4_batch, dict) else 1

    retrieval_cache = vector_search._ensure_retrieval_cache(state.get("_retrieval_cache"))
    state["_retrieval_cache"] = retrieval_cache

    try:
        node_result, usage_totals, llm_calls_made = await run_parent_chunk_constraint_verifier_batch(
            state,
            file_filtering_result=latest_node4_batch if isinstance(latest_node4_batch, dict) else {},
            batch_id=batch_id,
        )
        log_modification_agent_search_group(
            run_id=run_id,
            step="parent_chunk_constraint_verifier",
            payload=node_result,
        )
        return {
            "node5_parent_chunk_constraint_verifier_result": node_result,
            "token_prompt_total": int(state.get("token_prompt_total", 0) or 0) + usage_totals["prompt_tokens"],
            "token_completion_total": int(state.get("token_completion_total", 0) or 0)
            + usage_totals["completion_tokens"],
            "token_total": int(state.get("token_total", 0) or 0) + usage_totals["total_tokens"],
            "llm_call_count": int(state.get("llm_call_count", 0) or 0) + llm_calls_made,
            "_retrieval_cache": retrieval_cache,
        }
    except Exception as error:
        error_message = f"parent_chunk_constraint_verifier node failed: {error}"
        print(error_message)
        log_modification_agent_search_group(
            run_id=run_id,
            step="parent_chunk_constraint_verifier",
            payload={"error": error_message},
        )
        return {
            "error": error_message,
        }
