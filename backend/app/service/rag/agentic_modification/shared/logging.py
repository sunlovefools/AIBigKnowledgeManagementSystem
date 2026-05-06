"""Logging adapters for Agentic Modification with backend/local fallback imports."""


def _noop(*_args, **_kwargs):
    return None


def _resolve_logging_functions():
    """Load debug logging helpers, tolerating partial test stubs."""
    module = None
    try:
        from backend.debug import debug_logger as module
    except Exception:
        try:
            from debug import debug_logger as module
        except Exception:
            module = None

    if module is None:
        return _noop, _noop, _noop, _noop

    return (
        getattr(module, "log_token_usage", _noop),
        getattr(module, "log_modification_agent_llm_request", _noop),
        getattr(module, "log_modification_agent_llm_response", _noop),
        getattr(module, "log_modification_agent_search_group", _noop),
    )


(
    log_token_usage,
    log_modification_agent_llm_request,
    log_modification_agent_llm_response,
    log_modification_agent_search_group,
) = _resolve_logging_functions()

