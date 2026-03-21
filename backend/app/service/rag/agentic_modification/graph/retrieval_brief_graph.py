"""
LangGraph assembly for Agentic Modification retrieval brief + search/group pipeline.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from ..nodes.editor_node import editor_node
from ..nodes.iterative_search_filter_orchestrator_node import (
    iterative_search_filter_orchestrator_node,
)
from ..nodes.retrieval_brief_extractor_node import retrieval_brief_extractor_node
from ..state.retrieval_brief_state import RetrievalBriefState


def build_retrieval_brief_graph():
    """Build graph with extractor + iterative orchestrator + editor node."""
    
    graph = StateGraph(RetrievalBriefState)
    graph.add_node("retrieval_brief_extractor", retrieval_brief_extractor_node)
    graph.add_node(
        "iterative_search_filter_orchestrator",
        iterative_search_filter_orchestrator_node,
    )
    graph.add_node("editor_node", editor_node)
    graph.set_entry_point("retrieval_brief_extractor")
    graph.add_edge("retrieval_brief_extractor", "iterative_search_filter_orchestrator")
    graph.add_edge("iterative_search_filter_orchestrator", "editor_node")
    graph.add_edge("editor_node", END)
    return graph.compile()


retrieval_brief_graph = build_retrieval_brief_graph()
