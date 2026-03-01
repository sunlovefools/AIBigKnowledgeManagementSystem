"""
API router for the Modification Agent pipeline.
POST /api/agent/modify
"""
from __future__ import annotations

import traceback
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

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
               PUT /api/modifications/parent-chunks/{parentId} on approve.
    """
    intention: str
    proposals: list[ProposalItem]


@router.get("/health")
def agent_health():
    return {"agent": "ok"}


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

    # Normalise: empty list → None (search all)
    file_ids = request.fileIds if request.fileIds else None

    initial_state = {
        "instruction": request.instruction.strip(),
        "file_ids": file_ids,
        "intention": "",
        "search_queries": [],
        "retrieved_chunks": [],
        "is_satisfied": False,
        "needs_expansion": False,
        "retry_count": 0,
        "proposals": [],
        "error": None,
    }

    print(f"\n{'='*50}")
    print(f"🚀 Agent pipeline started")
    print(f"   Instruction: {request.instruction}")
    scope = f"{len(file_ids)} file(s)" if file_ids else "all files"
    print(f"   Scope: {scope}")
    print(f"{'='*50}")

    try:
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