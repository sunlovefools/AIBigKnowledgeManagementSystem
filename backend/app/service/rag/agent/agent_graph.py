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
    """
    Routing logic after the context critic node, based on whether retrieved context is sufficient and the agent's intention.
    """
    is_satisfied = state.get("is_satisfied", True)
    retry_count = state.get("retry_count", 0)
    intention = state.get("intention", "edit")

    # Change the context_expansion to true if the intention is edit, otherwise route to display_locate if locate.
    if is_satisfied:
        return "context_expansion" if intention == "edit" else "display_locate"

    # If not satisfied and retry count is less than max, go back to queries creation to try again.
    if retry_count < AGENT_MAX_RETRIES:
        print(f"   Context insufficient — retrying ({retry_count}/{AGENT_MAX_RETRIES})...")
        return "queries_creation" # Route back to queries creation for another retrieval attempt.

    # Else, the max retries has been reached
    print("   Max retries reached — proceeding with available context.")
    return "context_expansion" if intention == "edit" else "display_locate"


def route_after_context_expansion(state: AgentState) -> str:
    """
    Routing logic after the context expansion node, based on whether further expansion is needed and the agent's intention.
    """
    needs_expansion = state.get("needs_expansion", False)
    retry_count = state.get("retry_count", 0)

    # If no further expansion is needed, route to patching regardless of intention.
    if needs_expansion and retry_count < AGENT_MAX_RETRIES:
        return "queries_creation"
    
    # If max retries has been reached or no further expansion is needed, route to patching.
    return "patching"


def build_agent_graph() -> StateGraph:
    """Assembles the LangGraph graph for the Modification Agent pipeline, defining nodes and routing logic."""

    # Define the graph with the AgentState as the shared state type.
    graph = StateGraph(AgentState)

    # Add all the nodes to the graph
    # The first argument is the node name explicitly defined by us
    # The second argument is the node function imported from agent_nodes.py which contains the logic for that step in the pipeline.
    graph.add_node("initial_interpretation", initial_interpretation_node)
    graph.add_node("queries_creation", queries_creation_node)
    graph.add_node("retrieve_chunks", retrieve_chunks_node)
    graph.add_node("context_critic", context_critic_node)
    graph.add_node("context_expansion", context_expansion_node)
    graph.add_node("patching", patching_node)
    graph.add_node("display_locate", display_locate_node)

    # Set entry point for the agent and define the edges between nodes which determine the flow of the pipeline.
    graph.set_entry_point("initial_interpretation")
    graph.add_edge("initial_interpretation", "queries_creation")
    graph.add_edge("queries_creation", "retrieve_chunks")
    graph.add_edge("retrieve_chunks", "context_critic")

    # Add conditional edges based on the output of the context critic
    graph.add_conditional_edges(
        "context_critic",
        route_after_context_critic, # The conditional routing function that determines the next node based on the state
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

    # Add the end node
    graph.add_edge("patching", END)
    graph.add_edge("display_locate", END)

    # Compile the graph
    return graph.compile()


# Compiled once on import
agent_graph = build_agent_graph()