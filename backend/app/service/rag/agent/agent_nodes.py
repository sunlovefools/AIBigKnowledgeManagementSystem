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

from .agent_state import AgentState, Proposal
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
_MAX_RETRIES = 3


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

async def _call_llm(
    system_prompt: str,
    user_message: str,
    *,
    run_id: str | None = None,
    step: str | None = None,
) -> tuple[str, dict[str, int]]:
    """Call DeepSeek (OpenAI-compatible) and return response text."""
    if not _DEEPSEEK_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set.")

    payload = {
        "model": _DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {_DEEPSEEK_KEY}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=120.0)
    async with aiohttp.ClientSession() as session:
        async with session.post(_DEEPSEEK_URL, json=payload, headers=headers, timeout=timeout) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"DeepSeek API error ({resp.status}): {text}")
            data = await resp.json()

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
    """Parse JSON from LLM response, stripping markdown fences if present."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(cleaned.strip())


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
            system_prompt=(
                "Classify the user instruction as 'edit' or 'locate'. Respond with one word only.\n"
                "'edit' means: modify, update, translate, rewrite, fix, change, summarise, add, remove, replace content.\n"
                "'locate' means: find, search, show, list, where is — purely read-only retrieval with no changes."
            ),
            user_message=INITIAL_INTERPRETATION_PROMPT.format(instruction=state["instruction"]),
            run_id=state.get("run_id"),
            step="initial_interpretation",
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
        # No file scope — use semantic search across all files
        for query in queries:
            try:
                chunks = await search_and_retrieve_context(query=query, top_k=5)
                for chunk in chunks:
                    chunk_id = chunk.get("id")
                    if chunk_id and chunk_id not in seen_ids:
                        seen_ids.add(chunk_id)
                        all_chunks.append(chunk)
            except Exception as e:
                print(f"   ⚠️  Search failed for '{query}': {e}")

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

async def patching_node(state: AgentState) -> dict:
    """Generate original/proposed modification pairs."""
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

    try:
        result, usage = await _call_llm(
            system_prompt="You are a precise document editor. Respond with only a JSON array of modification objects.",
            user_message=PATCHING_PROMPT.format(
                instruction=state["instruction"],
                chunks_json=json.dumps(chunks_for_prompt, ensure_ascii=False, indent=2),
            ),
            run_id=state.get("run_id"),
            step="patching",
        )
        raw_proposals = _parse_json(result)
        if not isinstance(raw_proposals, list):
            raw_proposals = []

        chunk_map: dict[str, dict] = {chunk.get("id", ""): chunk for chunk in chunks}
        proposals: list[Proposal] = []

        for item in raw_proposals:
            parent_id = item.get("parentId", "")
            chunk = chunk_map.get(parent_id, {})
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
                original=item.get("original", ""),
                proposed=item.get("proposed", ""),
            ))

        print(f"   Generated {len(proposals)} proposal(s).")
        return {"proposals": proposals, **_accumulate_usage(state, usage)}
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