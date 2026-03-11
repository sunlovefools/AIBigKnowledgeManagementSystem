"""
LangGraph state definition for the Modification Agent pipeline.
Passed between all nodes in the graph.
"""
from __future__ import annotations
from typing import Any, Optional
from typing_extensions import TypedDict

# B04: single source of truth for the retry limit.
# Previously defined separately in agent_nodes.py AND agent_graph.py,
# risking the two values drifting out of sync.
AGENT_MAX_RETRIES: int = 3


class Proposal(TypedDict):
    """A single modification proposal for one parent chunk."""
    fileId: str
    fileName: str
    parentId: str
    original: str
    proposed: str


class AgentState(TypedDict):
    """Shared state passed through the entire LangGraph agent pipeline."""

    # --- Input ---
    instruction: str                    # User's modification instruction
    file_ids: Optional[list[str]]       # None = all files, list = scoped to these fileIds
    run_id: str                         # Correlation ID for one pipeline run

    # --- Intermediate ---
    intention: str                      # "edit" or "locate"
    search_queries: list[str]           # Generated search queries
    retrieved_chunks: list[dict[str, Any]]  # Raw chunks from vector search
    is_satisfied: bool                  # Retrieved context sufficient?
    needs_expansion: bool               # Need more context before editing?
    retry_count: int                    # Retrieval retry counter
    token_prompt_total: int             # Accumulated prompt tokens across LLM calls
    token_completion_total: int         # Accumulated completion tokens across LLM calls
    token_total: int                    # Accumulated total tokens across LLM calls
    llm_call_count: int                 # Number of successful LLM calls with usage data

    # --- Infrastructure ---
    # B03: shared aiohttp.ClientSession for the entire pipeline run.
    # Created once in router_agent.py before ainvoke, passed through state so
    # all LLM nodes reuse the same connection pool instead of creating 5 sessions.
    # Typed as Any to avoid importing aiohttp here.
    _session: Optional[Any]

    # --- Output ---
    proposals: list[Proposal]           # Final proposals returned to frontend
    error: Optional[str]