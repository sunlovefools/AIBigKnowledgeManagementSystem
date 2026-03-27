"""
API router for the Agentic Modification pipeline.
Canonical endpoint: POST /api/agent/modify
"""
from __future__ import annotations

import asyncio
import json
import traceback
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.core.dependencies import get_current_user
from app.service.collection.collection_service import (
    CollectionNotFoundError,
    CollectionService,
    CollectionServiceError,
)

try:
    from backend.debug.debug_logger import log_token_usage
except ImportError:
    from debug.debug_logger import log_token_usage

router = APIRouter()

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


class ProposalItem(BaseModel):
    fileId: str
    fileName: str
    parentId: str
    original: str
    proposed: str
    source: Optional[Literal["agent", "selection"]] = None
    selectionStart: Optional[int] = None
    selectionEnd: Optional[int] = None


class AgenticModificationRequest(BaseModel):
    """Request payload for Agentic Modification retrieval brief extraction."""

    user_instructions: str
    fileIds: Optional[list[str]] = None
    collectionId: Optional[str] = None


class AgenticModificationResponse(BaseModel):
    """Response payload for Agentic Modification retrieval brief extraction."""

    intention: str
    proposals: list[ProposalItem]
    goal: str
    lexical_anchors: list[str]
    semantic_anchors: list[str]
    anchors: list[str]
    constraint: str
    node2_search_group_result: dict[str, Any]
    node3_non_strong_signal_file_context_expansion_result: dict[str, Any]
    node4_file_filtering_result: dict[str, Any]
    node5_parent_chunk_constraint_verifier_result: dict[str, Any]
    node6_editor_result: dict[str, Any]


@router.get("/health")
def agent_health():
    return {"agent": "ok"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_progress_event(
    *,
    stage: str,
    status_value: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Unified progress payload shape consumed by frontend stream parser.
    event: dict[str, Any] = {
        "stage": stage,
        "status": status_value,
        "message": message,
        "timestamp": _now_iso(),
    }
    if isinstance(metadata, dict) and metadata:
        event["metadata"] = metadata
    return event


def _format_sse(event_name: str, data: dict[str, Any]) -> str:
    # SSE frame format: one event block separated by a blank line.
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_name}\ndata: {payload}\n\n"


def _load_retrieval_graph():
    try:
        from app.service.rag.agentic_modification.graph.retrieval_brief_graph import (
            retrieval_brief_graph,
        )
        return retrieval_brief_graph
    except ModuleNotFoundError as exc:
        # Only support local fallback when package root `app` itself is missing.
        # Do not swallow real dependency/import errors from inside the module.
        if exc.name != "app":
            raise
        from graph.retrieval_brief_graph import retrieval_brief_graph
        return retrieval_brief_graph


def _build_agentic_response(final_state: dict[str, Any], *, run_id: str) -> AgenticModificationResponse:
    if final_state.get("error"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agentic Modification error: {final_state['error']}",
        )

    goal = str(final_state.get("goal", "") or "").strip()
    lexical_anchors = [
        str(anchor).strip()
        for anchor in (final_state.get("lexical_anchors", []) or [])
        if str(anchor).strip()
    ]
    semantic_anchors = [
        str(anchor).strip()
        for anchor in (final_state.get("semantic_anchors", []) or [])
        if str(anchor).strip()
    ]
    anchors = [
        str(anchor).strip()
        for anchor in (final_state.get("anchors", []) or [])
        if str(anchor).strip()
    ]
    constraint = str(final_state.get("constraint", "None") or "").strip() or "None"

    node2_search_group_result = final_state.get("node2_search_group_result", {})
    if not isinstance(node2_search_group_result, dict):
        node2_search_group_result = {}

    node3_non_strong_signal_file_context_expansion_result = final_state.get(
        "node3_non_strong_signal_file_context_expansion_result",
        {},
    )
    if not isinstance(node3_non_strong_signal_file_context_expansion_result, dict):
        node3_non_strong_signal_file_context_expansion_result = {}

    node4_file_filtering_result = final_state.get("node4_file_filtering_result", {})
    if not isinstance(node4_file_filtering_result, dict):
        node4_file_filtering_result = {}

    node5_parent_chunk_constraint_verifier_result = final_state.get(
        "node5_parent_chunk_constraint_verifier_result",
        {},
    )
    if not isinstance(node5_parent_chunk_constraint_verifier_result, dict):
        node5_parent_chunk_constraint_verifier_result = {}

    node6_editor_result = final_state.get("node6_editor_result", {})
    if not isinstance(node6_editor_result, dict):
        node6_editor_result = {}

    intention = str(final_state.get("intention", "edit") or "").strip() or "edit"
    proposals_raw = final_state.get("proposals", [])
    proposals_list: list[ProposalItem] = []

    if isinstance(proposals_raw, list):
        for item in proposals_raw:
            if not isinstance(item, dict):
                continue

            file_id = str(item.get("fileId") or "").strip()
            file_name = str(item.get("fileName") or "").strip() or "unknown"
            parent_id = str(item.get("parentId") or "").strip()
            original = str(item.get("original") or "")
            proposed = str(item.get("proposed") or "")
            source_raw = str(item.get("source") or "").strip().lower()
            source: Literal["agent", "selection"] | None = None
            if source_raw in {"agent", "selection"}:
                source = source_raw
            selection_start = item.get("selectionStart")
            selection_end = item.get("selectionEnd")
            selection_start_int = int(selection_start) if isinstance(selection_start, int) else None
            selection_end_int = int(selection_end) if isinstance(selection_end, int) else None
            if not file_id or not parent_id:
                continue

            proposals_list.append(
                ProposalItem(
                    fileId=file_id,
                    fileName=file_name,
                    parentId=parent_id,
                    original=original,
                    proposed=proposed,
                    source=source,
                    selectionStart=selection_start_int,
                    selectionEnd=selection_end_int,
                )
            )

    token_prompt_total = int(final_state.get("token_prompt_total", 0) or 0)
    token_completion_total = int(final_state.get("token_completion_total", 0) or 0)
    token_total = int(final_state.get("token_total", 0) or 0)
    llm_call_count = int(final_state.get("llm_call_count", 0) or 0)

    log_token_usage(
        provider="OPENROUTER",
        model="modification-agent-run",
        prompt_tokens=token_prompt_total,
        completion_tokens=token_completion_total,
        total_tokens=token_total,
        estimated_cost_usd=0.0,
        operation="modification_agent_run",
        run_id=run_id,
        step=f"llm_calls={llm_call_count}",
    )

    print(
        "[Agentic Modification] Pipeline complete - "
        f"lexical={len(lexical_anchors)} semantic={len(semantic_anchors)}"
    )

    return AgenticModificationResponse(
        intention=intention,
        proposals=proposals_list,
        goal=goal,
        lexical_anchors=lexical_anchors,
        semantic_anchors=semantic_anchors,
        anchors=anchors,
        constraint=constraint,
        node2_search_group_result=node2_search_group_result,
        node3_non_strong_signal_file_context_expansion_result=node3_non_strong_signal_file_context_expansion_result,
        node4_file_filtering_result=node4_file_filtering_result,
        node5_parent_chunk_constraint_verifier_result=node5_parent_chunk_constraint_verifier_result,
        node6_editor_result=node6_editor_result,
    )


async def _run_agentic_pipeline(
    request: AgenticModificationRequest,
    user_id: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> AgenticModificationResponse:
    """
    Run Agentic Modification retrieval brief + search/group pipeline.
    """
    if not request.user_instructions.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="user_instructions must not be empty.",
        )

    retrieval_brief_graph = _load_retrieval_graph()

    import aiohttp

    run_id = uuid4().hex
    try:
        active_collection = await CollectionService.resolve_active_collection(
            user_id=user_id,
            requested_collection_id=request.collectionId,
        )
        scoped_file_ids = set(
            await CollectionService.list_file_ids_for_collection(
                user_id=user_id,
                collection_id=str(active_collection.get("collection_id") or ""),
            )
        )
    except CollectionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except CollectionServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Collection service error: {str(e)}",
        )

    requested_file_ids = {
        str(file_id or "").strip()
        for file_id in (request.fileIds or [])
        if str(file_id or "").strip()
    }
    if requested_file_ids:
        final_file_ids = sorted(scoped_file_ids.intersection(requested_file_ids))
    else:
        final_file_ids = sorted(scoped_file_ids)
    file_ids = final_file_ids if final_file_ids else ["__empty_scope__"]
    initial_state = {
        "user_instructions": request.user_instructions.strip(),
        "user_id": user_id,
        "run_id": run_id,
        "file_ids": file_ids,
        "intention": "edit",
        "goal": "",
        "lexical_anchors": [],
        "semantic_anchors": [],
        "anchors": [],
        "constraint": "None",
        "node2_search_group_result": {},
        "node3_non_strong_signal_file_context_expansion_result": {},
        "node4_file_filtering_result": {},
        "node5_parent_chunk_constraint_verifier_result": {},
        "node6_editor_result": {},
        "proposals": [],
        "token_prompt_total": 0,
        "token_completion_total": 0,
        "token_total": 0,
        "llm_call_count": 0,
        "error": None,
        "_session": None,
        "_retrieval_cache": {},
        "_progress_callback": progress_callback,
    }

    print("[Agentic Modification] Retrieval brief pipeline started")
    print(f"[Agentic Modification] User instructions: {request.user_instructions}")
    if progress_callback:
        # Top-level start event for chat UX before individual nodes emit updates.
        await progress_callback(
            _build_progress_event(
                stage="agentic_pipeline",
                status_value="started",
                message="Agentic modification pipeline started.",
                metadata={"runId": run_id},
            )
        )

    try:
        async with aiohttp.ClientSession() as session:
            initial_state["_session"] = session
            final_state = await retrieval_brief_graph.ainvoke(initial_state)
    except Exception as e:
        traceback.print_exc()
        if progress_callback:
            # Keep error signaling consistent with successful SSE runs.
            await progress_callback(
                _build_progress_event(
                    stage="agentic_pipeline",
                    status_value="failed",
                    message="Agentic modification pipeline failed.",
                    metadata={"runId": run_id, "error": str(e)},
                )
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agentic Modification pipeline failed: {str(e)}",
        )

    response = _build_agentic_response(final_state, run_id=run_id)
    if progress_callback:
        # Top-level completion event after model response has been normalized.
        await progress_callback(
            _build_progress_event(
                stage="agentic_pipeline",
                status_value="completed",
                message="Agentic modification pipeline completed.",
                metadata={"runId": run_id, "proposalCount": len(response.proposals)},
            )
        )
    return response


@router.post("/modify", response_model=AgenticModificationResponse)
@router.post("/v2/modify", response_model=AgenticModificationResponse)
async def agentic_modify(
    request: AgenticModificationRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user.get("sub") or "").strip() if isinstance(current_user, dict) else ""
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    try:
        return await _run_agentic_pipeline(request, user_id=user_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agentic Modification pipeline failed: {str(exc)}",
        )


# -- Main entry point for the agentic modification pipeline, with SSE streaming of progress updates and final result --
@router.post("/modify-stream")
async def agentic_modify_stream(
    request: AgenticModificationRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user.get("sub") or "").strip() if isinstance(current_user, dict) else ""
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    # Queue decouples producer task (pipeline) from StreamingResponse consumer.
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _push_progress(event_payload: dict[str, Any]) -> None:
        await queue.put(_format_sse("progress", event_payload))

    async def _runner() -> None:
        try:
            response = await _run_agentic_pipeline(
                request,
                user_id=user_id,
                progress_callback=_push_progress,
            )
            # Send final response as explicit result frame.
            await queue.put(_format_sse("result", response.model_dump()))
        except HTTPException as exc:
            await queue.put(
                _format_sse(
                    "error",
                    {
                        "statusCode": int(exc.status_code),
                        "detail": str(exc.detail),
                    },
                )
            )
        except Exception as exc:
            await queue.put(
                _format_sse(
                    "error",
                    {
                        "statusCode": status.HTTP_500_INTERNAL_SERVER_ERROR,
                        "detail": str(exc),
                    },
                )
            )
        finally:
            # Sentinel indicating stream completion.
            await queue.put(None)

    task = asyncio.create_task(_runner())

    async def _event_stream():
        try:
            while True:
                next_item = await queue.get()
                if next_item is None:
                    break
                yield next_item
        finally:
            # Client disconnect should stop the background runner promptly.
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
