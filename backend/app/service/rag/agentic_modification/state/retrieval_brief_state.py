"""
LangGraph state definition for the Agentic Modification retrieval brief + search/group pipeline.
"""
from __future__ import annotations

from typing import Any, Optional

from typing_extensions import NotRequired, TypedDict


class RetrievalBriefState(TypedDict):
    """Shared state for Agentic Modification nodes."""

    # --- Input ---
    user_instructions: str
    user_id: str
    run_id: str
    file_ids: list[str] | None

    # --- Intermediate / Output ---
    # Node 1 output
    intention: str
    goal: str
    lexical_anchors: list[str]
    semantic_anchors: list[str]
    anchors: list[str]
    constraint: str
    # Node 2..6 outputs
    node2_search_group_result: dict[str, Any] # Might need a better name
    node3_non_strong_signal_file_context_expansion_result: dict[str, Any]
    node4_file_filtering_result: dict[str, Any]
    node5_parent_chunk_constraint_verifier_result: dict[str, Any]
    node6_editor_result: dict[str, Any]
    proposals: list[dict[str, Any]]
    error: Optional[str]

    # --- Observability ---
    token_prompt_total: int
    token_completion_total: int
    token_total: int
    llm_call_count: int

    # --- Infrastructure ---
    _session: Optional[Any]
    _retrieval_cache: Optional[Any]
    # Optional async callback injected by stream endpoints to receive live stage updates.
    _progress_callback: NotRequired[Any]
