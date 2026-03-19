"""
LangGraph state definition for the Agent v2 retrieval brief pipeline.
"""
from __future__ import annotations

from typing import Any, Optional
from typing_extensions import TypedDict


class RetrievalBriefState(TypedDict):
    """Shared state for the single-node retrieval brief extraction graph."""

    # --- Input ---
    user_instructions: str
    run_id: str

    # --- Intermediate / Output ---
    goal: str
    anchors: list[str]
    constraint: str
    error: Optional[str]

    # --- Observability ---
    token_prompt_total: int
    token_completion_total: int
    token_total: int
    llm_call_count: int

    # --- Infrastructure ---
    _session: Optional[Any]

