"""
API router for the Agentic Modification pipeline.
Canonical endpoint: POST /api/agent/modify
Compatibility alias: POST /api/agent/v2/modify
"""
from __future__ import annotations

import traceback
from typing import Any, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

try:
    from backend.debug.debug_logger import log_token_usage
except ImportError:
    from debug.debug_logger import log_token_usage

router = APIRouter()


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


@router.post("/modify", response_model=AgenticModificationResponse)
@router.post("/v2/modify", response_model=AgenticModificationResponse)
async def agentic_modify(request: AgenticModificationRequest):
    """
    Run Agentic Modification retrieval brief + search/group pipeline.
    """
    if not request.user_instructions.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="user_instructions must not be empty.",
        )

    try:
        from app.service.rag.agentic_modification.graph.retrieval_brief_graph import (
            retrieval_brief_graph,
        )
    except ModuleNotFoundError as exc:
        # Only support local fallback when package root `app` itself is missing.
        # Do not swallow real dependency/import errors from inside the module.
        if exc.name != "app":
            raise
        from graph.retrieval_brief_graph import retrieval_brief_graph

    import aiohttp

    run_id = uuid4().hex
    file_ids = request.fileIds if request.fileIds else None
    initial_state = {
        "user_instructions": request.user_instructions.strip(),
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
    }

    print("[Agentic Modification] Retrieval brief pipeline started")
    print(f"[Agentic Modification] User instructions: {request.user_instructions}")

    try:
        async with aiohttp.ClientSession() as session:
            initial_state["_session"] = session
            final_state = await retrieval_brief_graph.ainvoke(initial_state)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agentic Modification pipeline failed: {str(e)}",
        )

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
