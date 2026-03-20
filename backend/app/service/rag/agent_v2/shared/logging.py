"""Logging adapters for Agent v2 with backend/local fallback imports."""

try:
    from backend.debug.debug_logger import (
        log_token_usage,
        log_modification_agent_llm_request,
        log_modification_agent_llm_response,
        log_modification_agent_search_group,
    )
except ImportError:
    from debug.debug_logger import (
        log_token_usage,
        log_modification_agent_llm_request,
        log_modification_agent_llm_response,
        log_modification_agent_search_group,
    )

