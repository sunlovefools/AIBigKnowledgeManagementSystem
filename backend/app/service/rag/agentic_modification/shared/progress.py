"""Progress event helper for Agentic Modification pipeline."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..state.retrieval_brief_state import RetrievalBriefState


async def emit_progress(
    state: RetrievalBriefState,
    *,
    stage: str,
    status: str,
    message: str,
    batch_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Best-effort progress emit that never interrupts pipeline execution."""
    callback = state.get("_progress_callback")
    if not callback or not callable(callback):
        # Non-stream requests do not set this callback.
        return

    event: dict[str, Any] = {
        "stage": stage,
        "status": status,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if isinstance(batch_id, int):
        event["batchId"] = batch_id
    if isinstance(metadata, dict) and metadata:
        event["metadata"] = metadata

    try:
        # Never block or fail core mutation flow due to progress transport issues.
        await callback(event)
    except Exception:
        # Progress transport failures must not fail the mutation pipeline.
        return
