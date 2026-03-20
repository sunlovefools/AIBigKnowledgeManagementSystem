"""
API router for the Modification Agent pipeline.
POST /api/agent/modify
"""
from __future__ import annotations

import traceback
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

try:
    from backend.debug.debug_logger import log_token_usage
except ImportError:
    from debug.debug_logger import log_token_usage

router = APIRouter()


class AgentModifyRequest(BaseModel):
    """
    instruction: Natural language edit instruction.
    fileIds: Optional list of fileIds to scope the search.
             - None or empty list = search all files
             - ["id1", "id2"] = search only these files
    """
    instruction: str
    fileIds: Optional[list[str]] = None


class ProposalItem(BaseModel):
    fileId: str
    fileName: str
    parentId: str
    original: str
    proposed: str


class AgentModifyResponse(BaseModel):
    """
    intention: "edit" or "locate"
    proposals: List of proposed modifications.
               Frontend renders diff view and calls
               POST /api/modifications/parent-chunks/batch-update on approve.
    """
    intention: str
    proposals: list[ProposalItem]


class AgentV2ModifyRequest(BaseModel):
    """Request payload for v2 retrieval brief extraction."""
    user_instructions: str


class AgentV2ModifyResponse(BaseModel):
    """Response payload for v2 retrieval brief extraction."""
    goal: str
    lexical_anchors: list[str]
    semantic_anchors: list[str]
    anchors: list[str]
    constraint: str
    node2_search_group_result: dict[str, Any]
    node3_non_strong_signal_file_context_expansion_result: dict[str, Any]
    node4_file_filtering_result: dict[str, Any]


@router.get("/health")
def agent_health():
    return {"agent": "ok"}


@router.post("/v2/modify", response_model=AgentV2ModifyResponse)
async def agent_v2_modify(request: AgentV2ModifyRequest):
    """
    Run Agent v2 retrieval brief + search/group pipeline.
    """
    if not request.user_instructions.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="user_instructions must not be empty.",
        )

    try:
        from app.service.rag.agent_v2.graph.retrieval_brief_graph import retrieval_brief_graph
    except ModuleNotFoundError as exc:
        # Only support local fallback when package root `app` itself is missing.
        # Do not swallow real dependency/import errors from inside the v2 module.
        if exc.name != "app":
            raise
        from graph.retrieval_brief_graph import retrieval_brief_graph

    import aiohttp

    run_id = uuid4().hex
    initial_state = {
        "user_instructions": request.user_instructions.strip(),
        "run_id": run_id,
        "goal": "",
        "lexical_anchors": [],
        "semantic_anchors": [],
        "anchors": [],
        "constraint": "None",
        "node2_search_group_result": {},
        "node3_non_strong_signal_file_context_expansion_result": {},
        "node4_file_filtering_result": {},
        "token_prompt_total": 0,
        "token_completion_total": 0,
        "token_total": 0,
        "llm_call_count": 0,
        "error": None,
        "_session": None,
        "_retrieval_cache": {},
    }

    print("[Agentic Modification V2] Retrieval brief pipeline started")
    print(f"[Agentic Modification V2] User instructions: {request.user_instructions}")

    try:
        async with aiohttp.ClientSession() as session:
            initial_state["_session"] = session
            final_state = await retrieval_brief_graph.ainvoke(initial_state)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent v2 pipeline failed: {str(e)}",
        )

    if final_state.get("error"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent v2 error: {final_state['error']}",
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

    token_prompt_total = int(final_state.get("token_prompt_total", 0) or 0)
    token_completion_total = int(final_state.get("token_completion_total", 0) or 0)
    token_total = int(final_state.get("token_total", 0) or 0)
    llm_call_count = int(final_state.get("llm_call_count", 0) or 0)

    log_token_usage(
        provider="OPENROUTER",
        model="modification-agent-v2-run",
        prompt_tokens=token_prompt_total,
        completion_tokens=token_completion_total,
        total_tokens=token_total,
        estimated_cost_usd=0.0,
        operation="modification_agent_v2_run",
        run_id=run_id,
        step=f"llm_calls={llm_call_count}",
    )

    print(
        "[Agentic Modification V2] Pipeline complete - "
        f"lexical={len(lexical_anchors)} semantic={len(semantic_anchors)}"
    )

    return AgentV2ModifyResponse(
        goal=goal,
        lexical_anchors=lexical_anchors,
        semantic_anchors=semantic_anchors,
        anchors=anchors,
        constraint=constraint,
        node2_search_group_result=node2_search_group_result,
        node3_non_strong_signal_file_context_expansion_result=node3_non_strong_signal_file_context_expansion_result,
        node4_file_filtering_result=node4_file_filtering_result,
    )


@router.post("/modify", response_model=AgentModifyResponse)
async def agent_modify(request: AgentModifyRequest):
    """
    Run the full Modification Agent pipeline.

    Scope:
    - fileIds = None or []  → search across ALL files
    - fileIds = ["id1",...] → search only specified files
    """
    if not request.instruction.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Instruction must not be empty.",
        )

    try:
        from app.service.rag.agent.agent_graph import agent_graph
    except ImportError:
        from agent_graph import agent_graph

    import aiohttp

    # Normalise: empty list → None (search all)
    file_ids = request.fileIds if request.fileIds else None
    run_id = uuid4().hex

    initial_state = {
        "instruction": request.instruction.strip(),
        "file_ids": file_ids,
        "run_id": run_id,
        "intention": "",
        "search_queries": [],
        "retrieved_chunks": [],
        "is_satisfied": False,
        "needs_expansion": False,
        "retry_count": 0,
        "token_prompt_total": 0,
        "token_completion_total": 0,
        "token_total": 0,
        "llm_call_count": 0,
        "proposals": [],
        "error": None,
        "_session": None,  # B03: populated below inside the session context manager
    }

    print(f"[Agentic Modification] Agent pipeline started")
    print(f"   Instruction: {request.instruction}")
    scope = f"{len(file_ids)} file(s)" if file_ids else "all files"
    print(f"   Scope: {scope}")
    print(f"{'='*50}")

    # B03: create one shared ClientSession for the entire pipeline run.
    # All LLM nodes (initial_interpretation, queries_creation, context_critic,
    # context_expansion, patching) reuse this session's connection pool instead
    # of each creating and immediately destroying their own.
    try:
        async with aiohttp.ClientSession() as session:
            initial_state["_session"] = session
            final_state = await agent_graph.ainvoke(initial_state)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent pipeline failed: {str(e)}",
        )

    if final_state.get("error"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent error: {final_state['error']}",
        )

    proposals = final_state.get("proposals", [])
    intention = final_state.get("intention", "edit")
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

    print(f"\n✅ Pipeline complete — {len(proposals)} proposal(s).")

    return AgentModifyResponse(
        intention=intention,
        proposals=[
            ProposalItem(
                fileId=p["fileId"],
                fileName=p["fileName"],
                parentId=p["parentId"],
                original=p["original"],
                proposed=p["proposed"],
            )
            for p in proposals
        ],
    )
