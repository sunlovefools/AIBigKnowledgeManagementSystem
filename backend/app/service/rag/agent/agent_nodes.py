"""
LangGraph node functions for the Modification Agent pipeline.

Execution model:
- LLM-backed nodes use DeepSeek through an OpenAI-compatible Chat Completions API.
- Non-LLM nodes reuse existing retrieval/reconstruction services.

Design goals:
- Keep each node deterministic in what it writes into graph state.
- Fail soft with explicit fallbacks where possible so the graph can continue.
- Track token usage centrally for run-level observability.
"""
from __future__ import annotations

import json
import os
from typing import Any

import aiohttp

try:
    from backend.debug.debug_logger import (
        log_token_usage,
        log_modification_agent_llm_request,
        log_modification_agent_llm_response,
    )
except ImportError:
    from debug.debug_logger import (
        log_token_usage,
        log_modification_agent_llm_request,
        log_modification_agent_llm_response,
    )

from .agent_state import AgentState, Proposal, AGENT_MAX_RETRIES
from .agent_prompts import (
    INITIAL_INTERPRETATION_PROMPT,
    QUERIES_CREATION_PROMPT,
    CONTEXT_CRITIC_PROMPT,
    CONTEXT_EXPANSION_PROMPT,
    PATCHING_PROMPT,
)

def _normalize_url(raw: str) -> str:
    """
    Normalize the base URL for the LLM API, ensuring it ends with /chat/completions.
    """
    url = (raw or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/chat/completions"

_DEEPSEEK_URL = _normalize_url(
    os.getenv("MOD_AGENT_LLM_URL", "https://api.deepseek.com/v1/chat/completions")
)
_DEEPSEEK_KEY = os.getenv("MOD_AGENT_LLM_KEY")
_DEEPSEEK_MODEL = os.getenv("MOD_AGENT_LLM_MODEL", "deepseek-chat")
# Single source of truth for LLM provider configuration used by all nodes.


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

async def _call_llm(
    system_prompt: str,
    user_message: str,
    *,
    session: aiohttp.ClientSession | None = None,
    run_id: str | None = None,
    step: str | None = None,
    max_tokens: int = 4096,
) -> tuple[str, dict[str, int]]:
    """Call DeepSeek (OpenAI-compatible) and return response text.

    B03: accepts an optional shared session. When provided the caller's
    connection pool is reused across all LLM calls in one pipeline run,
    avoiding the overhead of creating/destroying a ClientSession per call.
    Falls back to creating its own session when none is supplied.

    max_tokens is always set explicitly to prevent the API from using its own
    default, which can silently truncate large JSON responses mid-string.

    Returns:
        tuple[str, dict[str, int]]: (assistant_text, normalized_usage_counters)
        where usage keys are prompt_tokens/completion_tokens/total_tokens.
    """
    if not _DEEPSEEK_KEY:
        raise RuntimeError("MOD_AGENT_LLM_KEY is not set.")

    payload = {
        "model": _DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {_DEEPSEEK_KEY}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=120.0)
    log_modification_agent_llm_request(
        provider="MOD_AGENT_LLM",
        model=_DEEPSEEK_MODEL,
        step=step,
        run_id=run_id,
        system_prompt=system_prompt,
        user_message=user_message,
    )

    # Internal request wrapper so session ownership logic stays outside.
    async def _do_request(session: aiohttp.ClientSession) -> dict:
        async with session.post(_DEEPSEEK_URL, json=payload, headers=headers, timeout=timeout) as resp:
            # Non-200 responses usually include useful provider-side details in body text.
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"DeepSeek API error ({resp.status}): {text}")
            return await resp.json()

    if session is not None:
        data = await _do_request(session)
    else:
        # If no session is provided, create one for this call and close it afterwards.
        async with aiohttp.ClientSession() as own_session:
            data = await _do_request(own_session)

    # Normalize usage fields because provider payloads can be missing or typed loosely.
    usage = data.get("usage") if isinstance(data, dict) else {}
    if not isinstance(usage, dict):
        usage = {}

    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    prompt_cache_hit_token = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
    prompt_cache_miss_token = int(usage.get("prompt_cache_miss_tokens", 0) or 0)
    prompt_tokens = prompt_cache_hit_token + prompt_cache_miss_token
    total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)

    # Cost estimate is logged for aggregate run-level spend visibility.
    estimate_cost = calculate_total_cost(prompt_cache_hit_token, prompt_cache_miss_token, completion_tokens)

    # Log the token usage for this LLM call, associating it with the current run and step for traceability.
    log_token_usage(
        provider="MOD_AGENT_LLM",
        model=_DEEPSEEK_MODEL,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=estimate_cost,
        operation="modification_agent_llm_call",
        run_id=run_id,
        step=step,
    )

    # Validate response shape before passing content to downstream node logic.
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("DeepSeek returned empty choices.")
    content = choices[0].get("message", {}).get("content", "").strip()
    if not content:
        raise RuntimeError("DeepSeek returned empty content.")
    log_modification_agent_llm_response(
        provider="MOD_AGENT_LLM",
        model=_DEEPSEEK_MODEL,
        step=step,
        run_id=run_id,
        response_text=content,
    )
    return content, {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }

def calculate_total_cost(prompt_cache_hit_tokens: int, prompt_cache_miss_tokens: int, completion_tokens: int) -> float:
    """
    Calculate the estimated cost of an LLM call based on token usage and DeepSeek's pricing.

    DeepSeek's pricing (as of March 7, 2026) is modeled here as:
    - USD 0.028 per 1,000,000 prompt tokens (cache hit)
    - USD 0.280 per 1,000,000 prompt tokens (cache miss)
    - USD 0.420 per 1,000,000 completion tokens

    This function applies these rates to the respective token counts to estimate the total cost.
    """

    # Constant rates for DeepSeek's pricing model
    cost_per_one_million_prompt_cache_hit_tokens = 0.028
    cost_per_one_million_prompt_cache_miss_tokens = 0.28
    cost_per_one_million_completion_tokens = 0.42

    total_cost = (
        (prompt_cache_hit_tokens / 1000000) * cost_per_one_million_prompt_cache_hit_tokens +
        (prompt_cache_miss_tokens / 1000000) * cost_per_one_million_prompt_cache_miss_tokens +
        (completion_tokens / 1000000) * cost_per_one_million_completion_tokens
    )

    return total_cost

def _parse_json(text: str) -> Any:
    """Parse JSON from LLM response, stripping markdown fences if present.

    Also attempts to recover from truncated responses: if the full parse fails,
    it walks back from the last closing bracket/brace to find the largest valid
    prefix â€” this salvages partial proposals instead of discarding the entire
    response when max_tokens is hit unexpectedly.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Walk back from the last ] or } and try to parse whatever is complete.
        for end_char in ("]", "}"):
            pos = cleaned.rfind(end_char)
            if pos != -1:
                try:
                    return json.loads(cleaned[: pos + 1])
                except json.JSONDecodeError:
                    continue
        raise


def _accumulate_usage(state: AgentState, usage: dict[str, int]) -> dict[str, int]:
    """Merge one LLM call usage report into cumulative totals in graph state."""
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)
    # Count only calls that actually reported non-zero usage.
    should_count_call = 1 if (prompt_tokens > 0 or completion_tokens > 0 or total_tokens > 0) else 0

    return {
        "token_prompt_total": int(state.get("token_prompt_total", 0) or 0) + prompt_tokens,
        "token_completion_total": int(state.get("token_completion_total", 0) or 0) + completion_tokens,
        "token_total": int(state.get("token_total", 0) or 0) + total_tokens,
        "llm_call_count": int(state.get("llm_call_count", 0) or 0) + should_count_call,
    }


# ------------------------------------------------------------------
# Node 1: Initial Interpretation Agent (LLM)
# ------------------------------------------------------------------

async def initial_interpretation_node(state: AgentState) -> dict:
    """Classify user intent as 'edit' or 'locate'.

    Returns:
        dict: {"intention": "edit"|"locate", ...usage counters}
    """
    print("[Agent 1] Interpreting intent...")
    try:
        result, usage = await _call_llm(
            # The full classification rules are already in INITIAL_INTERPRETATION_PROMPT.
            # A minimal system prompt avoids double-spending tokens on repeated instructions.
            system_prompt="You are an intent classifier. Respond with one word only: 'edit' or 'locate'.",
            user_message=INITIAL_INTERPRETATION_PROMPT.format(instruction=state["instruction"]),
            session=state.get("_session"),
            run_id=state.get("run_id"),
            step="initial_interpretation",
            max_tokens=10,  # One word reply no need for more
        )

        # Clean the intention string
        intention = result.strip().lower()

        # Safety default: unknown labels route to edit flow (the broader path).
        if intention not in ("edit", "locate"):
            intention = "edit"
        print(f"   Intent: {intention}")
        return {"intention": intention, **_accumulate_usage(state, usage)}
    except Exception as error:
        print(f"Intent classification failed: {error}. Defaulting to 'edit'.")
        # Hard fallback keeps graph execution moving even if LLM is unavailable.
        return {"intention": "edit"}


# ------------------------------------------------------------------
# Node 2: Queries Creation Agent (LLM)
# ------------------------------------------------------------------

async def queries_creation_node(state: AgentState) -> dict:
    """Generate semantic search queries from the instruction.

    Returns:
        dict: {"search_queries": list[str], "retry_count": int, ...usage counters}
    """
    retry_count = state.get("retry_count", 0)
    previous_queries = state.get("search_queries", [])
    print(f"[Agent 2] Generating queries (attempt {retry_count + 1})...")
    try:
        result, usage = await _call_llm(
            system_prompt="Generate search queries. Respond with only a JSON array of strings.",
            user_message=QUERIES_CREATION_PROMPT.format(
                instruction=state["instruction"],
                previous_queries=json.dumps(previous_queries) if previous_queries else "None",
            ),
            session=state.get("_session"),
            run_id=state.get("run_id"),
            step="queries_creation",
        )
        queries = _parse_json(result)

        # Guard against malformed model output by collapsing to a single fallback query.
        if not isinstance(queries, list):
            queries = [state["instruction"]]
        # Normalize to strings and drop empty elements.
        queries = [str(q) for q in queries if q]
        print(f"   Queries: {queries}")
        return {
            "search_queries": queries,
            "retry_count": retry_count + 1,
            **_accumulate_usage(state, usage),
        }
    except Exception as error:
        print(f"    Query generation failed: {error}. Using instruction as fallback.")
        # Fallback query still allows retrieval to proceed.
        return {"search_queries": [state["instruction"]], "retry_count": retry_count + 1}


# ------------------------------------------------------------------
# Node 3: Retrieve Chunks (Non-LLM)
# ------------------------------------------------------------------

async def retrieve_chunks_node(state: AgentState) -> dict:
    """Retrieve candidate parent chunks for downstream analysis/editing.

    Retrieval strategy:
    - If `file_ids` is provided, load those files directly (strictly scoped mode).
    - Otherwise run semantic vector search for each generated query (global mode).
    """
    from app.vectordb.vectordb import search_and_retrieve_context
    from app.service.modification.reconstruction_service import ReconstructionService

    print("[Node 3] Retrieving chunks...")
    queries = state.get("search_queries", [])
    file_ids_filter = state.get("file_ids")  # None = all files, list = scoped

    all_chunks: list[dict] = []
    # Deduplicate by parent chunk id across files/queries/pagination pages.
    seen_ids: set[str] = set()

    if file_ids_filter:
        # When specific files are selected, load their chunks directly.
        # Semantic search would generate queries based on the instruction (e.g. "ç¿»è¯‘æ–¹æ³•")
        # rather than the file content, missing the target files entirely.
        print(f"   Loading chunks directly for {len(file_ids_filter)} scoped file(s)...")

        # Build a fileId â†’ fileName map from the sidebar file list
        # Fetch only the specific files' names â€” avoids full-collection scan
        # which triggers AstraDB ClosedConnectionException on large stores.
        file_id_to_name = await ReconstructionService.get_file_names_by_ids(file_ids_filter)

        for file_id in file_ids_filter:
            file_name = file_id_to_name.get(file_id, "unknown")
            try:
                cursor = None
                while True:
                    # Cursor pagination is required for files with many parent chunks.
                    result = await ReconstructionService.get_file_parent_chunks(
                        file_id=file_id, limit=20, cursor=cursor
                    )
                    for chunk in result.get("chunks", []):
                        parent_id = chunk.get("parentId", "")
                        if parent_id and parent_id not in seen_ids:
                            seen_ids.add(parent_id)
                            # Normalise to the same shape search_and_retrieve_context returns
                            all_chunks.append({
                                "id": parent_id,
                                "page_content": chunk.get("content", ""),
                                "metadata": {
                                    "file_metadata": {
                                        "file_id": file_id,
                                        "file_name": file_name,
                                    },
                                },
                            })
                    if not result.get("hasMore"):
                        break
                    # Continue scanning the same file until all parent chunks are loaded.
                    cursor = result.get("nextCursor")
            except Exception as e:
                print(f"    Direct load failed for file_id={file_id}: {e}")
    else:
        # No file scope use semantic search across all files.
        # B05: fire all queries in parallel with asyncio.gather instead of
        # awaiting them one-by-one. 3 queries Ã— ~300ms each = ~900ms serial
        # vs ~300ms parallel â€” the longest single query sets the total time.
        import asyncio

        async def _search_one(query: str) -> list[dict]:
            try:
                return await search_and_retrieve_context(query=query, top_k=5)
            except Exception as e:
                # Query-level failure should not fail the whole node.
                print(f"    Search failed for '{query}': {e}")
                return []

        results = await asyncio.gather(*[_search_one(q) for q in queries])
        for chunks in results:
            for chunk in chunks:
                chunk_id = chunk.get("id")
                if chunk_id and chunk_id not in seen_ids:
                    seen_ids.add(chunk_id)
                    all_chunks.append(chunk)

    print(f"   Retrieved {len(all_chunks)} unique parent chunks.")
    return {"retrieved_chunks": all_chunks}


# ------------------------------------------------------------------
# Node 4: Context Critic Decision Agent (LLM)
# ------------------------------------------------------------------

async def context_critic_node(state: AgentState) -> dict:
    """Judge whether retrieved chunks are sufficient for confident editing/locating."""
    print("[Agent 4] Evaluating context quality...")
    chunks = state.get("retrieved_chunks", [])
    if not chunks:
        print("   No chunks retrieved â€” unsatisfied.")
        return {"is_satisfied": False}

    chunks_summary = "\n\n".join([
        # Only summarize a small window to keep prompt cost and latency bounded.
        f"[Chunk {i+1} | {c.get('metadata', {}).get('file_metadata', {}).get('file_name', 'unknown')}]\n"
        f"{c.get('page_content', '')[:300]}..."
        for i, c in enumerate(chunks[:5])
    ])
    try:
        result, usage = await _call_llm(
            system_prompt='Evaluate context quality. Respond with only {"satisfied": true} or {"satisfied": false}.',
            user_message=CONTEXT_CRITIC_PROMPT.format(
                instruction=state["instruction"],
                chunks_summary=chunks_summary,
            ),
            session=state.get("_session"),
            run_id=state.get("run_id"),
            step="context_critic",
        )
        satisfied = bool(_parse_json(result).get("satisfied", True))
        print(f"   Satisfied: {satisfied}")
        return {"is_satisfied": satisfied, **_accumulate_usage(state, usage)}
    except Exception as error:
        print(f"   âŒ Context critic failed: {error}. Defaulting to satisfied.")
        # Defaulting to satisfied avoids endless retrieval loops on transient LLM errors.
        return {"is_satisfied": True}


# ------------------------------------------------------------------
# Node 5: Context Expansion Decision Agent (LLM)
# ------------------------------------------------------------------

async def context_expansion_node(state: AgentState) -> dict:
    """Decide if more surrounding context is needed before patch generation."""
    print("ðŸ¤– [Agent 5] Checking context expansion need...")
    chunks = state.get("retrieved_chunks", [])
    chunks_summary = "\n\n".join([
        # Same truncation strategy as critic node for stable token usage.
        f"[Chunk {i+1}]\n{c.get('page_content', '')[:300]}..."
        for i, c in enumerate(chunks[:5])
    ])
    try:
        result, usage = await _call_llm(
            system_prompt='Decide if more context is needed. Respond with only {"needed": true} or {"needed": false}.',
            user_message=CONTEXT_EXPANSION_PROMPT.format(
                instruction=state["instruction"],
                chunks_summary=chunks_summary,
            ),
            session=state.get("_session"),
            run_id=state.get("run_id"),
            step="context_expansion",
        )
        needed = bool(_parse_json(result).get("needed", False))
        print(f"   Expansion needed: {needed}")
        return {"needs_expansion": needed, **_accumulate_usage(state, usage)}
    except Exception as e:
        print(f"   âŒ Context expansion failed: {e}. Defaulting to not needed.")
        # Conservative fallback: proceed with available context.
        return {"needs_expansion": False}


# ------------------------------------------------------------------
# Node 6: Patching Agent (LLM)
# ------------------------------------------------------------------

# Max chunks sent to the patching LLM per batch.
# Keeping batches small prevents oversized JSON responses that exceed max_tokens
# and get truncated mid-string (the root cause of "Unterminated string" errors).
_PATCHING_BATCH_SIZE = 5


async def patching_node(state: AgentState) -> dict:
    """Generate original/proposed modification pairs.

    Chunks are processed in parallel batches of _PATCHING_BATCH_SIZE to prevent
    LLM response truncation that occurs when all chunks are serialised into one
    giant prompt.  Each batch gets its own LLM call with a generous max_tokens
    budget, and results are merged before validation.
    """
    import asyncio

    print("[Agent 6] Generating modification proposals...")
    chunks = state.get("retrieved_chunks", [])

    # Convert retrieval payload to the compact schema expected by PATCHING_PROMPT.
    chunks_for_prompt = [
        {
            "parentId": chunk.get("id", ""),
            "fileName": (
                chunk.get("metadata", {}).get("file_metadata", {}).get("file_name")
                or chunk.get("metadata", {}).get("file_name")
                or "unknown"
            ),
            "content": chunk.get("page_content", ""),
        }
        for chunk in chunks
    ]

    # Partition into bounded-size batches to reduce truncation risk.
    batches = [
        chunks_for_prompt[i: i + _PATCHING_BATCH_SIZE]
        for i in range(0, max(len(chunks_for_prompt), 1), _PATCHING_BATCH_SIZE)
    ]
    print(f"   Processing {len(chunks_for_prompt)} chunk(s) across {len(batches)} batch(es)...")

    async def _call_batch(batch: list[dict]) -> tuple[list, dict]:
        # Each batch is an independent call so partial failures can be tolerated.
        result, usage = await _call_llm(
            system_prompt="You are a precise document editor. Respond with only a JSON array of modification objects.",
            user_message=PATCHING_PROMPT.format(
                instruction=state["instruction"],
                chunks_json=json.dumps(batch, ensure_ascii=False, indent=2),
            ),
            session=state.get("_session"),
            run_id=state.get("run_id"),
            step="patching",
            max_tokens=8192,  # Generous budget per batch each batch is small
        )
        parsed = _parse_json(result)
        return (parsed if isinstance(parsed, list) else []), usage

    batch_results = await asyncio.gather(
        *[_call_batch(b) for b in batches],
        return_exceptions=True,
    )

    raw_proposals: list[dict] = []
    combined_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for i, res in enumerate(batch_results):
        if isinstance(res, Exception):
            # Keep successful batches; skip only the failed shard.
            print(f"    Batch {i + 1} failed: {res}")
            continue
        proposals_batch, usage = res
        raw_proposals.extend(proposals_batch)
        # Aggregate usage so the node returns one merged usage increment.
        for k in combined_usage:
            combined_usage[k] += usage.get(k, 0)

    try:
        if not isinstance(raw_proposals, list):
            raw_proposals = []

        chunk_map: dict[str, dict] = {chunk.get("id", ""): chunk for chunk in chunks}
        proposals: list[Proposal] = []

        skipped = 0
        for item in raw_proposals:
            parent_id = item.get("parentId", "")
            original = item.get("original", "")
            proposed = item.get("proposed", "")

            # B02: validate parentId refers to a real chunk we actually retrieved.
            # LLM sometimes hallucinates parentIds that don't exist in chunk_map.
            chunk = chunk_map.get(parent_id)
            if chunk is None:
                print(f"    Skipping proposal â€” parentId '{parent_id}' not in retrieved chunks (hallucinated).")
                skipped += 1
                continue

            # B02: validate original text truly exists inside the chunk content.
            # Without this, the frontend would display a diff with text that isn't
            # in the document, and accepting it would write corrupt content to the DB.
            chunk_content = chunk.get("page_content", "")
            if not original:
                print(f"    Skipping proposal â€” empty original text for parentId '{parent_id}'.")
                skipped += 1
                continue
            if original not in chunk_content:
                print(f"    Skipping proposal â€” original text not found in chunk '{parent_id}'. "
                      f"LLM may have hallucinated: {original[:80]!r}")
                skipped += 1
                continue

            metadata = chunk.get("metadata", {})
            file_metadata = metadata.get("file_metadata", {})
            # Prefer retrieval metadata; fall back to LLM-provided name if missing.
            file_name = (
                file_metadata.get("file_name")
                or chunk.get("metadata", {}).get("file_name")
                or item.get("fileName", "unknown")
            )
            proposals.append(Proposal(
                fileId=file_metadata.get("file_id", ""),
                fileName=file_name,
                parentId=parent_id,
                original=original,
                proposed=proposed,
            ))

        if skipped:
            print(f"    {skipped} proposal(s) skipped due to validation failures.")
        print(f"    Generated {len(proposals)} proposal(s).")
        return {"proposals": proposals, **_accumulate_usage(state, combined_usage)}
    except Exception as e:
        print(f"    Patching agent failed: {e}")
        return {"proposals": [], "error": str(e)}


# ------------------------------------------------------------------
# Node 7: Display Node (Non-LLM) â€” locate path
# ------------------------------------------------------------------

async def display_locate_node(state: AgentState) -> dict:
    """Format retrieved chunks as locate-only proposals (original == proposed)."""
    print("ðŸ“‹ [Node 7] Formatting locate results...")
    chunks = state.get("retrieved_chunks", [])
    locate_results: list[Proposal] = []

    for chunk in chunks:
        metadata = chunk.get("metadata", {})
        file_metadata = metadata.get("file_metadata", {})
        content = chunk.get("page_content", "")
        # Reuse Proposal shape so frontend can render locate/edit results uniformly.
        locate_results.append(Proposal(
            fileId=file_metadata.get("file_id", ""),
            fileName=file_metadata.get("file_name",
                     metadata.get("file_name", "unknown")),
            parentId=chunk.get("id", ""),
            original=content,
            proposed=content,
        ))

    print(f"   Returning {len(locate_results)} locate result(s).")
    return {"proposals": locate_results}
