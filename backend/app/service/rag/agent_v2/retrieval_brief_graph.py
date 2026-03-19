"""
LangGraph assembly for Agent v2 retrieval brief pipeline.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from .retrieval_brief_nodes import retrieval_brief_extractor_node
from .retrieval_brief_state import RetrievalBriefState


def build_retrieval_brief_graph():
    """Build single-node graph for retrieval brief extraction."""
    graph = StateGraph(RetrievalBriefState)
    graph.add_node("retrieval_brief_extractor", retrieval_brief_extractor_node)
    graph.set_entry_point("retrieval_brief_extractor")
    graph.add_edge("retrieval_brief_extractor", END)
    return graph.compile()


retrieval_brief_graph = build_retrieval_brief_graph()
