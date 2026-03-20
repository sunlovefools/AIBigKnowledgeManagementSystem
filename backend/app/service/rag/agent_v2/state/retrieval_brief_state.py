"""
LangGraph state definition for the Agent v2 retrieval brief + search/group pipeline.
"""
from __future__ import annotations

from typing import Any, Optional

from typing_extensions import TypedDict


class RetrievalBriefState(TypedDict):
    """Shared state for Agent v2 nodes."""

    # --- Input ---
    user_instructions: str
    run_id: str

    # --- Intermediate / Output ---
    goal: str
    lexical_anchors: list[str]
    semantic_anchors: list[str]
    anchors: list[str]
    constraint: str
    node2_search_group_result: dict[str, Any] # Might need a better name
    node3_non_strong_signal_file_context_expansion_result: dict[str, Any]
    node4_file_filtering_result: dict[str, Any]
    error: Optional[str]

    # --- Observability ---
    token_prompt_total: int
    token_completion_total: int
    token_total: int
    llm_call_count: int

    # --- Infrastructure ---
    _session: Optional[Any]
    _retrieval_cache: Optional[Any]
