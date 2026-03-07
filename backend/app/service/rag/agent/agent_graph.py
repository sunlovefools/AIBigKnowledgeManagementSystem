"""
LangGraph graph assembly for the Modification Agent pipeline.
"""
from __future__ import annotations

from langgraph.graph import StateGraph, END

from .agent_state import AgentState, AGENT_MAX_RETRIES
from .agent_nodes import (
    initial_interpretation_node,
    queries_creation_node,
    retrieve_chunks_node,
    context_critic_node,
    context_expansion_node,
    patching_node,
    display_locate_node,
)


def route_after_context_critic(state: AgentState) -> str:
    is_satisfied = state.get("is_satisfied", True)
    retry_count = state.get("retry_count", 0)
    intention = state.get("intention", "edit")

    if is_satisfied:
        return "context_expansion" if intention == "edit" else "display_locate"

    if retry_count < AGENT_MAX_RETRIES:
        print(f"   Context insufficient — retrying ({retry_count}/{AGENT_MAX_RETRIES})...")
        return "queries_creation"

    print("   Max retries reached — proceeding with available context.")
    return "context_expansion" if intention == "edit" else "display_locate"


def route_after_context_expansion(state: AgentState) -> str:
    needs_expansion = state.get("needs_expansion", False)
    retry_count = state.get("retry_count", 0)
    if needs_expansion and retry_count < AGENT_MAX_RETRIES:
        return "queries_creation"
    return "patching"


def build_agent_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("initial_interpretation", initial_interpretation_node)
    graph.add_node("queries_creation", queries_creation_node)
    graph.add_node("retrieve_chunks", retrieve_chunks_node)
    graph.add_node("context_critic", context_critic_node)
    graph.add_node("context_expansion", context_expansion_node)
    graph.add_node("patching", patching_node)
    graph.add_node("display_locate", display_locate_node)

    graph.set_entry_point("initial_interpretation")
    graph.add_edge("initial_interpretation", "queries_creation")
    graph.add_edge("queries_creation", "retrieve_chunks")
    graph.add_edge("retrieve_chunks", "context_critic")

    graph.add_conditional_edges(
        "context_critic",
        route_after_context_critic,
        {
            "queries_creation": "queries_creation",
            "context_expansion": "context_expansion",
            "display_locate": "display_locate",
        },
    )
    graph.add_conditional_edges(
        "context_expansion",
        route_after_context_expansion,
        {
            "queries_creation": "queries_creation",
            "patching": "patching",
        },
    )
    graph.add_edge("patching", END)
    graph.add_edge("display_locate", END)

    return graph.compile()


# Compiled once on import
agent_graph = build_agent_graph()