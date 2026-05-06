"""LangGraph nodes for Agentic Modification.

This module exports both:
- canonical module names matching the file names (`node_X_*`)
- compatibility aliases used by existing tests/callers
"""

from importlib import import_module

# Canonical module exports (match file names)
node_1_retrieval_brief_extractor = import_module(".node_1_retrieval_brief_extractor", __name__)
node_2_search_and_group = import_module(".node_2_search_and_group", __name__)
node_2_5_iterative_search_filter_orchestrator = import_module(
    ".orchestration_node_iterative_search_filter_orchestrator",
    __name__,
)
node_3_non_strong_signal_file_context_expansion = import_module(
    ".node_3_non_strong_signal_file_context_expansion",
    __name__,
)
node_4_file_filtering = import_module(".node_4_file_filtering", __name__)
node_5_parent_chunk_constraint_verifier = import_module(
    ".node_5_parent_chunk_constraint_verifier",
    __name__,
)
node_6_editor = import_module(".node_6_editor", __name__)

# Compatibility aliases to avoid breaking importers/tests that still use old module names.
retrieval_brief_extractor_node = node_1_retrieval_brief_extractor
search_and_group_node = node_2_search_and_group
iterative_search_filter_orchestrator_node = node_2_5_iterative_search_filter_orchestrator
non_strong_signal_file_context_expansion_node = node_3_non_strong_signal_file_context_expansion
file_filtering_node = node_4_file_filtering
parent_chunk_constraint_verifier_node = node_5_parent_chunk_constraint_verifier
editor_node = node_6_editor

__all__ = [
    "node_1_retrieval_brief_extractor",
    "node_2_search_and_group",
    "node_2_5_iterative_search_filter_orchestrator",
    "node_3_non_strong_signal_file_context_expansion",
    "node_4_file_filtering",
    "node_5_parent_chunk_constraint_verifier",
    "node_6_editor",
    "retrieval_brief_extractor_node",
    "search_and_group_node",
    "iterative_search_filter_orchestrator_node",
    "non_strong_signal_file_context_expansion_node",
    "file_filtering_node",
    "parent_chunk_constraint_verifier_node",
    "editor_node",
]
