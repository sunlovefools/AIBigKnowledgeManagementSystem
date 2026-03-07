"""
LangGraph node functions for the Modification Agent pipeline.
LLM nodes call DeepSeek via OpenAI-compatible API.
Non-LLM nodes call existing RAG infrastructure.
"""
from __future__ import annotations

import json
import os
from typing import Any

import aiohttp

try:
    from backend.debug.debug_logger import log_token_usage
except ImportError:
    from debug.debug_logger import log_token_usage

from .agent_state import AgentState, Proposal, AGENT_MAX_RETRIES
from .agent_prompts import (
    INITIAL_INTERPRETATION_PROMPT,
    QUERIES_CREATION_PROMPT,
    CONTEXT_CRITIC_PROMPT,
    CONTEXT_EXPANSION_PROMPT,
    PATCHING_PROMPT,
)

def _normalize_url(raw: str) -> str:
    url = (raw or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/chat/completions"

_DEEPSEEK_URL = _normalize_url(
    os.getenv("OPENROUTER_URL", "https://api.deepseek.com/v1/chat/completions")
)
_DEEPSEEK_KEY = os.getenv("OPENROUTER_API_KEY")
_DEEPSEEK_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek-chat")


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
    """
    if not _DEEPSEEK_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set.")

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

    async def _do_request(s: aiohttp.ClientSession) -> dict:
        async with s.post(_DEEPSEEK_URL, json=payload, headers=headers, timeout=timeout) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"DeepSeek API error ({resp.status}): {text}")
            return await resp.json()

    if session is not None:
        data = await _do_request(session)
    else:
        async with aiohttp.ClientSession() as own_session:
            data = await _do_request(own_session)

    usage = data.get("usage") if isinstance(data, dict) else {}
    if not isinstance(usage, dict):
        usage = {}

    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)

    log_token_usage(
        provider="OPENROUTER",
        model=_DEEPSEEK_MODEL,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=0.0,
        operation="modification_agent_llm_call",
        run_id=run_id,
        step=step,
    )

    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("DeepSeek returned empty choices.")
    content = choices[0].get("message", {}).get("content", "").strip()
    if not content:
        raise RuntimeError("DeepSeek returned empty content.")
    return content, {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _parse_json(text: str) -> Any:
    """Parse JSON from LLM response, stripping markdown fences if present.

    Also attempts to recover from truncated responses: if the full parse fails,
    it walks back from the last closing bracket/brace to find the largest valid
    prefix — this salvages partial proposals instead of discarding the entire
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
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)
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
    """Classify user intent as 'edit' or 'locate'."""
    print("🤖 [Agent 1] Interpreting intent...")
    try:
        result, usage = await _call_llm(
            # The full classification rules are already in INITIAL_INTERPRETATION_PROMPT.
            # A minimal system prompt avoids double-spending tokens on repeated instructions.
            system_prompt="You are an intent classifier. Respond with one word only: 'edit' or 'locate'.",
            user_message=INITIAL_INTERPRETATION_PROMPT.format(instruction=state["instruction"]),
            session=state.get("_session"),
            run_id=state.get("run_id"),
            step="initial_interpretation",
            max_tokens=10,  # One word reply — no need for more
        )
        intention = result.strip().lower()
        if intention not in ("edit", "locate"):
            intention = "edit"
        print(f"   Intent: {intention}")
        return {"intention": intention, **_accumulate_usage(state, usage)}
    except Exception as e:
        print(f"   ❌ Intent classification failed: {e}. Defaulting to 'edit'.")
        return {"intention": "edit"}


# ------------------------------------------------------------------
# Node 2: Queries Creation Agent (LLM)
# ------------------------------------------------------------------

async def queries_creation_node(state: AgentState) -> dict:
    """Generate search queries from the instruction."""
    retry_count = state.get("retry_count", 0)
    previous_queries = state.get("search_queries", [])
    print(f"🤖 [Agent 2] Generating queries (attempt {retry_count + 1})...")
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
        if not isinstance(queries, list):
            queries = [state["instruction"]]
        queries = [str(q) for q in queries if q]
        print(f"   Queries: {queries}")
        return {
            "search_queries": queries,
            "retry_count": retry_count + 1,
            **_accumulate_usage(state, usage),
        }
    except Exception as e:
        print(f"   ❌ Query generation failed: {e}. Using instruction as fallback.")
        return {"search_queries": [state["instruction"]], "retry_count": retry_count + 1}


# ------------------------------------------------------------------
# Node 3: Retrieve Chunks (Non-LLM)
# ------------------------------------------------------------------

async def retrieve_chunks_node(state: AgentState) -> dict:
    """Execute vector search, or direct file load when fileIds are scoped."""
    from app.vectordb.vectordb import search_and_retrieve_context
    from app.service.modification.reconstruction_service import ReconstructionService

    print("🔍 [Node 3] Retrieving chunks...")
    queries = state.get("search_queries", [])
    file_ids_filter = state.get("file_ids")  # None = all files, list = scoped

    all_chunks: list[dict] = []
    seen_ids: set[str] = set()

    if file_ids_filter:
        # When specific files are selected, load their chunks directly.
        # Semantic search would generate queries based on the instruction (e.g. "翻译方法")
        # rather than the file content, missing the target files entirely.
        print(f"   Loading chunks directly for {len(file_ids_filter)} scoped file(s)...")

        # Build a fileId → fileName map from the sidebar file list
        # Fetch only the specific files' names — avoids full-collection scan
        # which triggers AstraDB ClosedConnectionException on large stores.
        file_id_to_name = await ReconstructionService.get_file_names_by_ids(file_ids_filter)

        for file_id in file_ids_filter:
            file_name = file_id_to_name.get(file_id, "unknown")
            try:
                cursor = None
                while True:
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
                    cursor = result.get("nextCursor")
            except Exception as e:
                print(f"   ⚠️  Direct load failed for file_id={file_id}: {e}")
    else:
        # No file scope — use semantic search across all files.
        # B05: fire all queries in parallel with asyncio.gather instead of
        # awaiting them one-by-one. 3 queries × ~300ms each = ~900ms serial
        # vs ~300ms parallel — the longest single query sets the total time.
        import asyncio

        async def _search_one(query: str) -> list[dict]:
            try:
                return await search_and_retrieve_context(query=query, top_k=5)
            except Exception as e:
                print(f"   ⚠️  Search failed for '{query}': {e}")
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
    """Judge whether retrieved chunks are sufficient."""
    print("🤖 [Agent 4] Evaluating context quality...")
    chunks = state.get("retrieved_chunks", [])
    if not chunks:
        print("   No chunks retrieved — unsatisfied.")
        return {"is_satisfied": False}

    chunks_summary = "\n\n".join([
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
    except Exception as e:
        print(f"   ❌ Context critic failed: {e}. Defaulting to satisfied.")
        return {"is_satisfied": True}


# ------------------------------------------------------------------
# Node 5: Context Expansion Decision Agent (LLM)
# ------------------------------------------------------------------

async def context_expansion_node(state: AgentState) -> dict:
    """Decide if more surrounding context is needed before editing."""
    print("🤖 [Agent 5] Checking context expansion need...")
    chunks = state.get("retrieved_chunks", [])
    chunks_summary = "\n\n".join([
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
        print(f"   ❌ Context expansion failed: {e}. Defaulting to not needed.")
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

    print("🤖 [Agent 6] Generating modification proposals...")
    chunks = state.get("retrieved_chunks", [])

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

    # Partition into batches
    batches = [
        chunks_for_prompt[i: i + _PATCHING_BATCH_SIZE]
        for i in range(0, max(len(chunks_for_prompt), 1), _PATCHING_BATCH_SIZE)
    ]
    print(f"   Processing {len(chunks_for_prompt)} chunk(s) across {len(batches)} batch(es)...")

    async def _call_batch(batch: list[dict]) -> tuple[list, dict]:
        result, usage = await _call_llm(
            system_prompt="You are a precise document editor. Respond with only a JSON array of modification objects.",
            user_message=PATCHING_PROMPT.format(
                instruction=state["instruction"],
                chunks_json=json.dumps(batch, ensure_ascii=False, indent=2),
            ),
            session=state.get("_session"),
            run_id=state.get("run_id"),
            step="patching",
            max_tokens=8192,  # Generous budget per batch — each batch is small
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
            print(f"   ⚠️  Batch {i + 1} failed: {res}")
            continue
        proposals_batch, usage = res
        raw_proposals.extend(proposals_batch)
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
                print(f"   ⚠️  Skipping proposal — parentId '{parent_id}' not in retrieved chunks (hallucinated).")
                skipped += 1
                continue

            # B02: validate original text truly exists inside the chunk content.
            # Without this, the frontend would display a diff with text that isn't
            # in the document, and accepting it would write corrupt content to the DB.
            chunk_content = chunk.get("page_content", "")
            if not original:
                print(f"   ⚠️  Skipping proposal — empty original text for parentId '{parent_id}'.")
                skipped += 1
                continue
            if original not in chunk_content:
                print(f"   ⚠️  Skipping proposal — original text not found in chunk '{parent_id}'. "
                      f"LLM may have hallucinated: {original[:80]!r}")
                skipped += 1
                continue

            metadata = chunk.get("metadata", {})
            file_metadata = metadata.get("file_metadata", {})
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
            print(f"   ⚠️  {skipped} proposal(s) skipped due to validation failures.")
        print(f"   Generated {len(proposals)} proposal(s).")
        return {"proposals": proposals, **_accumulate_usage(state, combined_usage)}
    except Exception as e:
        print(f"   ❌ Patching agent failed: {e}")
        return {"proposals": [], "error": str(e)}


# ------------------------------------------------------------------
# Node 7: Display Node (Non-LLM) — locate path
# ------------------------------------------------------------------

async def display_locate_node(state: AgentState) -> dict:
    """Format retrieved chunks as locate results (no modifications)."""
    print("📋 [Node 7] Formatting locate results...")
    chunks = state.get("retrieved_chunks", [])
    locate_results: list[Proposal] = []

    for chunk in chunks:
        metadata = chunk.get("metadata", {})
        file_metadata = metadata.get("file_metadata", {})
        content = chunk.get("page_content", "")
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