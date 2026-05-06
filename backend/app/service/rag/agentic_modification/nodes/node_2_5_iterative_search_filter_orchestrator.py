"""Compatibility shim for legacy node 2.5 module path.

The orchestrator implementation was moved to
`orchestration_node_iterative_search_filter_orchestrator.py`, but some
importers may still reference this legacy module name directly.
"""

from .orchestration_node_iterative_search_filter_orchestrator import *  # noqa: F401,F403
