"""
LangGraph assembly for Agentic Modification retrieval brief + search/group pipeline.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from ..nodes.orchestration_node_iterative_search_filter_orchestrator import (
    iterative_search_filter_orchestrator_node,
)
from ..nodes.node_1_retrieval_brief_extractor import retrieval_brief_extractor_node
from ..nodes.node_6_editor import editor_node
from ..state.retrieval_brief_state import RetrievalBriefState


def build_retrieval_brief_graph():
    """Build graph with extractor + iterative orchestrator + editor node."""

    # Graph order:
    # 1) extract retrieval brief
    # 2) iterative retrieval/filter/verification
    # 3) final editing over confirmed parent chunks
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
