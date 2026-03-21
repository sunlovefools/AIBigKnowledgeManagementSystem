"""Node 5: explore clue chunks to confirm editable parent chunks."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..prompts.retrieval_brief_prompts import (
    CLUE_CHUNK_EXPLORER_SYSTEM_PROMPT,
    CLUE_CHUNK_EXPLORER_USER_PROMPT,
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
    """
    Normalize chunk numbers from various raw formats into a clean list of integers.
    """
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
        # Remove dupliactes
        if chunk_number in seen:
            continue
        seen.add(chunk_number)
        normalized.append(chunk_number)
    return normalized


def _normalize_tool_request(raw_parsed: dict[str, Any]) -> dict[str, Any] | None:
    """
    Validate and normalize the tool request from the LLM output and get the parameters for the tool call.
    The expected tool request format is:

    {
    "action": "tool",
    "tool_name": "get_parent_chunks" or "get_surrounding_parent_chunks",
    "arguments": {
        "start_chunk_number": int,  # for get_parent_chunks
        "end_chunk_number": int,    # for get_parent_chunks
        "chunk_number": int,        # for get_surrounding_parent_chunks
    }
    """
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


async def _run_one_clue_exploration(
    state: RetrievalBriefState,
    *,
    goal: str,
    constraint: str,
    file_id: str,
    file_name: str,
    clue_chunk_number: int,
    retrieval_cache: dict[str, Any],
    max_tool_rounds: int = 6, # To prevent infinite loops between the LLM and tools
) -> tuple[dict[str, Any], dict[str, int], int]:
    """
    Run one clue-chunk exploration, which may involve multiple rounds of LLM interaction and tool calls.
    """
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

    # Fetch the origin parent chunk for the clue chunk from the cache
    origin_parent_chunks = await vector_search._get_parent_chunks_for_file_range(
        file_id=file_id,
        start_chunk_number=clue_chunk_number,
        end_chunk_number=clue_chunk_number,
        cache=retrieval_cache,
    )
    # Extract the parent chunk number and cache the parent chunk by its number for easy lookup in the exploration rounds
    # TODO: Why do we need another cache?
    if isinstance(origin_parent_chunks, list):
        for parent_chunk in origin_parent_chunks:
            if not isinstance(parent_chunk, dict):
                continue
            chunk_number = parent_chunk.get("chunk_number")
            if isinstance(chunk_number, int):
                known_parent_chunks_by_number[chunk_number] = parent_chunk

    # Ensure the chunk number of the origin clue chunk is in the known parent chunks
    if clue_chunk_number not in known_parent_chunks_by_number:
        return (
            {
                "file_id": file_id,
                "file_name": file_name,
                "clue_chunk_number": clue_chunk_number,
                "origin_chunk_found": False,
                "confirmed_parent_chunk_numbers": [],
                "clue_outcome": "dead_end",
                "reasoning_summary": "Origin clue chunk is unavailable for this file.",
                "llm_round_count": 0,
                "tool_call_count": 0,
                "tool_history": [],
            },
            usage_totals,
            llm_calls_made,
        )

    last_reasoning = "No final clue decision produced."

    # Iteratively call the LLM to explore the file structure around the clue chunk
    for round_index in range(1, max_tool_rounds + 1):
        parent_chunks_payload = _build_parent_chunks_prompt_payload(
            [
                known_parent_chunks_by_number[number]
                for number in sorted(known_parent_chunks_by_number.keys())
                if isinstance(known_parent_chunks_by_number.get(number), dict)
            ]
        )
        tool_history_payload = json.dumps(tool_history, ensure_ascii=False) if tool_history else "[]"

        # The logging of the LLM call is handled in the llm client
        llm_text, usage = await llm_client._call_llm(
            system_prompt=CLUE_CHUNK_EXPLORER_SYSTEM_PROMPT,
            user_message=CLUE_CHUNK_EXPLORER_USER_PROMPT.format( # Format the prompt with dynamic values
                goal=goal,
                constraint=constraint,
                file_id=file_id,
                clue_chunk_number=clue_chunk_number,
                parent_chunks=parent_chunks_payload,
                tool_history=tool_history_payload,
            ),
            session=session,
            run_id=run_id,
            step="clue_chunk_explorer",
            max_tokens=512,
        )
        llm_calls_made += 1
        usage_totals["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
        usage_totals["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
        usage_totals["total_tokens"] += int(usage.get("total_tokens", 0) or 0)

        # Parse the LLM output
        try:
            raw_parsed = _parse_json_object(llm_text)
        except Exception:
            last_reasoning = "Model output was not valid JSON."
            break
        
        # Case 1: The LLM provides a final answer with confirmed parent chunk numbers, clue outcome and reasoning summary
        if (
            "confirmed_parent_chunk_numbers" in raw_parsed
            or "clue_outcome" in raw_parsed
            or "reasoning_summary" in raw_parsed
        ):
            candidate_numbers = _normalize_chunk_numbers(raw_parsed.get("confirmed_parent_chunk_numbers"))

            # Fetch the parent chunk details for the confirmed parent chunk numbers to include in the final result
            resolved_confirmed_map = await vector_search._fetch_parent_chunks_for_file_chunk_numbers(
                file_id=file_id,
                chunk_numbers=candidate_numbers,
                cache=retrieval_cache,
            )
            confirmed_numbers = sorted(resolved_confirmed_map.keys())
            for chunk_number, parent_chunk in resolved_confirmed_map.items():
                known_parent_chunks_by_number[chunk_number] = parent_chunk

            clue_outcome_raw = str(raw_parsed.get("clue_outcome") or "").strip().lower()
            clue_outcome = "confirmed" if confirmed_numbers else "dead_end"
            if clue_outcome_raw in {"confirmed", "dead_end"} and not (
                clue_outcome_raw == "confirmed" and not confirmed_numbers
            ):
                clue_outcome = clue_outcome_raw

            reasoning_summary = str(raw_parsed.get("reasoning_summary") or "").strip()
            if not reasoning_summary:
                reasoning_summary = (
                    "Confirmed editable parent chunks."
                    if confirmed_numbers
                    else "No editable parent chunk found for this clue."
                )

            return (
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "clue_chunk_number": clue_chunk_number,
                    "origin_chunk_found": True,
                    "confirmed_parent_chunk_numbers": confirmed_numbers,
                    "clue_outcome": clue_outcome,
                    "reasoning_summary": reasoning_summary,
                    "llm_round_count": round_index,
                    "tool_call_count": len(tool_history),
                    "tool_history": tool_history,
                },
                usage_totals,
                llm_calls_made,
            )

        # Case 2: The LLM provides a tool request to get more parent chunk information to explore in the next round
        tool_request = _normalize_tool_request(raw_parsed)
        if not isinstance(tool_request, dict):
            last_reasoning = "Model output did not match tool or final schema."
            break

        tool_name = tool_request["tool_name"]
        arguments = tool_request["arguments"]

        # Execute the tool request and get the result for the next round of LLM input
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
            "clue_chunk_number": clue_chunk_number,
            "origin_chunk_found": True,
            "confirmed_parent_chunk_numbers": [],
            "clue_outcome": "dead_end",
            "reasoning_summary": last_reasoning,
            "llm_round_count": llm_calls_made,
            "tool_call_count": len(tool_history),
            "tool_history": tool_history,
        },
        usage_totals,
        llm_calls_made,
    )


async def run_clue_chunk_explorer_batch(
    state: RetrievalBriefState,
    *,
    file_filtering_result: dict[str, Any],
    batch_id: int | None = None,
) -> tuple[dict[str, Any], dict[str, int], int]:
    """
    Reusable clue-chunk exploration batch runner.
    Returns:
      (node_result, usage_totals, llm_calls_made)
    """
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

    exploration_inputs: list[dict[str, Any]] = []
    seen_input_keys: set[str] = set()
    for evaluation in evaluations:
        if not isinstance(evaluation, dict):
            continue
        if str(evaluation.get("decision") or "").strip().lower() == "reject":
            continue
        file_id = str(evaluation.get("file_id") or "").strip()
        file_name = str(evaluation.get("file_name") or "").strip() or "unknown"
        if not file_id:
            continue
        clue_chunk_numbers = _normalize_chunk_numbers(evaluation.get("suggested_chunk_numbers"))
        for clue_chunk_number in clue_chunk_numbers:
            input_key = f"{file_id}::{clue_chunk_number}"
            if input_key in seen_input_keys:
                continue
            seen_input_keys.add(input_key)
            exploration_inputs.append(
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "clue_chunk_number": clue_chunk_number,
                }
            )

    if not exploration_inputs:
        node_result = {
            "batch_id": resolved_batch_id,
            "goal": goal,
            "constraint": constraint,
            "explorations": [],
            "confirmed_parent_chunks_by_file": [],
            "merged_confirmed_parent_chunk_refs": [],
            "run_summary": {
                "exploration_count": 0,
                "confirmed_exploration_count": 0,
                "dead_end_count": 0,
                "confirmed_file_count": 0,
                "confirmed_parent_chunk_ref_count": 0,
                "llm_round_count": 0,
                "tool_call_count": 0,
                "cache_stats": vector_search._snapshot_retrieval_cache_stats(retrieval_cache),
            },
        }
        return node_result, usage_totals, llm_calls_made

    exploration_results_raw = await asyncio.gather(
        *[
            _run_one_clue_exploration(
                state,
                goal=goal,
                constraint=constraint,
                file_id=item["file_id"],
                file_name=item["file_name"],
                clue_chunk_number=item["clue_chunk_number"],
                retrieval_cache=retrieval_cache,
            )
            for item in exploration_inputs
        ],
        return_exceptions=True,
    )

    explorations: list[dict[str, Any]] = []
    exploration_errors: list[dict[str, Any]] = []

    for input_item, raw_result in zip(exploration_inputs, exploration_results_raw):
        if isinstance(raw_result, Exception):
            exploration_errors.append(
                {
                    "file_id": input_item["file_id"],
                    "file_name": input_item["file_name"],
                    "clue_chunk_number": input_item["clue_chunk_number"],
                    "error": str(raw_result),
                }
            )
            explorations.append(
                {
                    "file_id": input_item["file_id"],
                    "file_name": input_item["file_name"],
                    "clue_chunk_number": input_item["clue_chunk_number"],
                    "origin_chunk_found": True,
                    "confirmed_parent_chunk_numbers": [],
                    "clue_outcome": "dead_end",
                    "reasoning_summary": f"Exploration failed: {raw_result}",
                    "llm_round_count": 0,
                    "tool_call_count": 0,
                    "tool_history": [],
                }
            )
            continue

        exploration_result, usage, llm_calls = raw_result
        usage_totals["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
        usage_totals["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
        usage_totals["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
        llm_calls_made += int(llm_calls or 0)
        explorations.append(exploration_result)

    confirmed_by_file_key: dict[str, dict[str, Any]] = {}
    merged_ref_by_key: dict[str, dict[str, Any]] = {}
    for exploration in explorations:
        if not isinstance(exploration, dict):
            continue
        file_id = str(exploration.get("file_id") or "").strip()
        file_name = str(exploration.get("file_name") or "").strip() or "unknown"
        confirmed_numbers = _normalize_chunk_numbers(exploration.get("confirmed_parent_chunk_numbers"))
        if not file_id or not confirmed_numbers:
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
        for number in confirmed_numbers:
            if number not in existing_numbers:
                existing_numbers.add(number)
                file_entry["confirmed_parent_chunk_numbers"].append(number)

        for number in confirmed_numbers:
            merged_ref_by_key.setdefault(
                f"{file_id}::{number}",
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "parent_chunk_number": number,
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
        "explorations": explorations,
        "confirmed_parent_chunks_by_file": confirmed_parent_chunks_by_file,
        "merged_confirmed_parent_chunk_refs": merged_confirmed_parent_chunk_refs,
        "errors": exploration_errors if exploration_errors else "none",
        "run_summary": {
            "exploration_count": len(explorations),
            "confirmed_exploration_count": sum(
                1
                for item in explorations
                if isinstance(item, dict) and str(item.get("clue_outcome") or "").strip().lower() == "confirmed"
            ),
            "dead_end_count": sum(
                1
                for item in explorations
                if isinstance(item, dict) and str(item.get("clue_outcome") or "").strip().lower() == "dead_end"
            ),
            "confirmed_file_count": len(confirmed_parent_chunks_by_file),
            "confirmed_parent_chunk_ref_count": len(merged_confirmed_parent_chunk_refs),
            "llm_round_count": sum(
                int(item.get("llm_round_count", 0) or 0)
                for item in explorations
                if isinstance(item, dict)
            ),
            "tool_call_count": sum(
                int(item.get("tool_call_count", 0) or 0)
                for item in explorations
                if isinstance(item, dict)
            ),
            "error_count": len(exploration_errors),
            "cache_stats": vector_search._snapshot_retrieval_cache_stats(retrieval_cache),
        },
    }
    return node_result, usage_totals, llm_calls_made


async def clue_chunk_explorer_node(state: RetrievalBriefState) -> dict:
    """Node 5 wrapper: run one clue-chunk exploration batch."""
    print("[Agent v2 - Node 5] Exploring clue chunks...")
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
        node_result, usage_totals, llm_calls_made = await run_clue_chunk_explorer_batch(
            state,
            file_filtering_result=latest_node4_batch if isinstance(latest_node4_batch, dict) else {},
            batch_id=batch_id,
        )
        log_modification_agent_search_group(
            run_id=run_id,
            step="clue_chunk_explorer",
            payload=node_result,
        )
        return {
            "node5_clue_chunk_explorer_result": node_result,
            "token_prompt_total": int(state.get("token_prompt_total", 0) or 0) + usage_totals["prompt_tokens"],
            "token_completion_total": int(state.get("token_completion_total", 0) or 0) + usage_totals["completion_tokens"],
            "token_total": int(state.get("token_total", 0) or 0) + usage_totals["total_tokens"],
            "llm_call_count": int(state.get("llm_call_count", 0) or 0) + llm_calls_made,
            "_retrieval_cache": retrieval_cache,
        }
    except Exception as error:
        error_message = f"clue_chunk_explorer node failed: {error}"
        print(error_message)
        log_modification_agent_search_group(
            run_id=run_id,
            step="clue_chunk_explorer",
            payload={"error": error_message},
        )
        return {
            "error": error_message,
        }
