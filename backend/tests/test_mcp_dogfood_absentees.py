import asyncio
import re
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI
from jose import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.mcp.auth import RAG_READ_SCOPE
from app.mcp import server as mcp_server
from app.mcp.models import (
    CollectionListResponse,
    CollectionSummary,
    EvidenceItem,
    FetchParentChunkResponse,
    ParentChunkContent,
    SearchMaterialsResponse,
)


async def _call_tool(
    session: ClientSession,
    trace: list[dict],
    name: str,
    arguments: dict | None = None,
) -> dict:
    result = await session.call_tool(name, arguments or {})
    structured = result.structuredContent or {}
    trace.append({"tool": name, "arguments": arguments or {}, "result": structured})
    return structured


def _extract_absentee_blocks(chunks: list[str]) -> str:
    blocks: list[str] = []
    for chunk in chunks:
        date_match = re.search(r"^Date:\s*(.+)$", chunk, flags=re.MULTILINE)
        if not date_match:
            continue
        date = date_match.group(1).strip()
        absentee_match = re.search(
            r"Absentees:\s*(.+?)(?:\n\s*\n|$)",
            chunk,
            flags=re.DOTALL,
        )
        if not absentee_match:
            continue
        names: list[str] = []
        for raw_line in absentee_match.group(1).splitlines():
            line = raw_line.strip().lstrip("-").strip()
            if not line:
                continue
            line = re.sub(r"^\d+[\).]\s*", "", line)
            names.append(line)
        if not names:
            continue
        lines = [f"Date: {date}", "Absentees:"]
        lines.extend(f"{index}. {name}" for index, name in enumerate(names, start=1))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def _codex_answer_absentees_with_mcp_tools(token: str) -> tuple[str, list[dict]]:
    trace: list[dict] = []
    test_mcp = mcp_server.create_rag_mcp()
    app = FastAPI()
    app.mount("/api/mcp", test_mcp.streamable_http_app())

    async with test_mcp.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=True,
        ) as http_client:
            async with streamable_http_client(
                "http://127.0.0.1:8000/api/mcp/",
                http_client=http_client,
                terminate_on_close=False,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    await _call_tool(session, trace, "list_collections")
                    search_result = await _call_tool(
                        session,
                        trace,
                        "search_materials",
                        {
                            "query": "meeting minutes attendance absentees reasons",
                            "searchScope": "all_collections",
                            "topK": 8,
                        },
                    )

                    chunks: list[str] = []
                    for item in search_result["evidence"]:
                        chunk_result = await _call_tool(
                            session,
                            trace,
                            "fetch_parent_chunk",
                            {
                                "parentId": item["parentId"],
                                "collectionId": item.get("collectionId"),
                            },
                        )
                        parent_chunk = chunk_result.get("parentChunk")
                        if parent_chunk and parent_chunk.get("content"):
                            chunks.append(parent_chunk["content"])

    return _extract_absentee_blocks(chunks), trace


def test_codex_dogfoods_mcp_tools_to_answer_absentees(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    token = jwt.encode(
        {
            "sub": "user-1",
            "email": "user@example.com",
            "role": "user",
            "scopes": [RAG_READ_SCOPE],
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        "test-secret",
        algorithm="HS256",
    )

    async def _fake_list_user_collections():
        return CollectionListResponse(
            collections=[
                CollectionSummary(
                    collectionId="collection-default",
                    name="Default",
                    isDefault=True,
                    fileCount=2,
                )
            ],
            total=1,
        )

    async def _fake_search_user_materials(**_kwargs):
        return SearchMaterialsResponse(
            query="meeting minutes attendance absentees reasons",
            searchScope="all_collections",
            evidence=[
                EvidenceItem(
                    parentId="meeting-1",
                    fileId="minutes-1",
                    fileName="meeting-2026-03-01.md",
                    collectionId="collection-default",
                    collectionName="Default",
                    parentChunkNumber=0,
                    snippet="Date: 2026-03-01 Absentees: Alice (Sick), Bob (Client call)",
                ),
                EvidenceItem(
                    parentId="meeting-2",
                    fileId="minutes-2",
                    fileName="meeting-2026-03-08.md",
                    collectionId="collection-default",
                    collectionName="Default",
                    parentChunkNumber=0,
                    snippet="Date: 2026-03-08 Absentees: Chen (Medical appointment)",
                ),
            ],
            total=2,
        )

    async def _fake_fetch_user_parent_chunk(parent_id: str, **_kwargs):
        contents = {
            "meeting-1": (
                "Date: 2026-03-01\n"
                "Topic: Sprint planning\n"
                "Absentees:\n"
                "- Alice (Sick)\n"
                "- Bob (Client call)\n\n"
                "Notes: Roadmap reviewed."
            ),
            "meeting-2": (
                "Date: 2026-03-08\n"
                "Topic: Retrospective\n"
                "Absentees:\n"
                "1. Chen (Medical appointment)\n\n"
                "Notes: Action items assigned."
            ),
        }
        return FetchParentChunkResponse(
            parentChunk=ParentChunkContent(
                parentId=parent_id,
                fileId=f"file-{parent_id}",
                fileName=f"{parent_id}.md",
                collectionId="collection-default",
                collectionName="Default",
                parentChunkNumber=0,
                content=contents[parent_id],
                truncated=False,
            )
        )

    monkeypatch.setattr(
        mcp_server.service,
        "list_user_collections",
        _fake_list_user_collections,
    )
    monkeypatch.setattr(
        mcp_server.service,
        "search_user_materials",
        _fake_search_user_materials,
    )
    monkeypatch.setattr(
        mcp_server.service,
        "fetch_user_parent_chunk",
        _fake_fetch_user_parent_chunk,
    )

    answer, trace = asyncio.run(_codex_answer_absentees_with_mcp_tools(token))

    assert answer == (
        "Date: 2026-03-01\n"
        "Absentees:\n"
        "1. Alice (Sick)\n"
        "2. Bob (Client call)\n\n"
        "Date: 2026-03-08\n"
        "Absentees:\n"
        "1. Chen (Medical appointment)"
    )
    assert [entry["tool"] for entry in trace] == [
        "list_collections",
        "search_materials",
        "fetch_parent_chunk",
        "fetch_parent_chunk",
    ]
