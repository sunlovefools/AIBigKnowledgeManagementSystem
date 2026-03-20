"""
LangGraph assembly for Agent v2 retrieval brief + search/group pipeline.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from ..nodes.iterative_search_filter_orchestrator_node import (
    iterative_search_filter_orchestrator_node,
)
from ..nodes.retrieval_brief_extractor_node import retrieval_brief_extractor_node
from ..state.retrieval_brief_state import RetrievalBriefState


def build_retrieval_brief_graph():
    """Build v2 graph with extractor + iterative search/filter orchestrator.
    TODO: We need to change the name in the future."""
    
    graph = StateGraph(RetrievalBriefState)
    graph.add_node("retrieval_brief_extractor", retrieval_brief_extractor_node)
    graph.add_node(
        "iterative_search_filter_orchestrator",
        iterative_search_filter_orchestrator_node,
    )
    graph.set_entry_point("retrieval_brief_extractor")
    graph.add_edge("retrieval_brief_extractor", "iterative_search_filter_orchestrator")
    graph.add_edge("iterative_search_filter_orchestrator", END)
    return graph.compile()


retrieval_brief_graph = build_retrieval_brief_graph()

