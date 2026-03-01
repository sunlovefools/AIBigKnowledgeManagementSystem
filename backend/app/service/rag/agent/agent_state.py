"""
LangGraph state definition for the Modification Agent pipeline.
Passed between all nodes in the graph.
"""
from __future__ import annotations
from typing import Any, Optional
from typing_extensions import TypedDict


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

    # --- Intermediate ---
    intention: str                      # "edit" or "locate"
    search_queries: list[str]           # Generated search queries
    retrieved_chunks: list[dict[str, Any]]  # Raw chunks from vector search
    is_satisfied: bool                  # Retrieved context sufficient?
    needs_expansion: bool               # Need more context before editing?
    retry_count: int                    # Retrieval retry counter

    # --- Output ---
    proposals: list[Proposal]           # Final proposals returned to frontend
    error: Optional[str]