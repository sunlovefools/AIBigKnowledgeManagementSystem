import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI
from jose import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.mcp.auth import RAG_READ_SCOPE
from app.mcp import server as mcp_server
from app.mcp.models import CollectionListResponse, CollectionSummary


def test_mcp_tools_are_registered():
    tools = asyncio.run(mcp_server.rag_mcp.list_tools())
    tool_names = {tool.name for tool in tools}

    assert {
        "list_collections",
        "describe_collection",
        "search_materials",
        "search_files",
        "fetch_parent_chunk",
        "fetch_file_outline",
    }.issubset(tool_names)


def test_mcp_tool_call_returns_structured_content(monkeypatch):
    async def _fake_list_user_collections():
        return CollectionListResponse(
            collections=[
                CollectionSummary(
                    collectionId="collection-default",
                    name="Default",
                    isDefault=True,
                    fileCount=3,
                )
            ],
            total=1,
        )

    monkeypatch.setattr(
        mcp_server.service,
        "list_user_collections",
        _fake_list_user_collections,
    )

    content, structured = asyncio.run(
        mcp_server.rag_mcp.call_tool("list_collections", {})
    )

    assert structured["total"] == 1
    assert structured["collections"][0]["collectionId"] == "collection-default"
    assert "collection-default" in content[0].text


def test_mcp_streamable_http_lists_and_calls_tools_with_bearer_auth(monkeypatch):
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
                    fileCount=3,
                )
            ],
            total=1,
        )

    monkeypatch.setattr(
        mcp_server.service,
        "list_user_collections",
        _fake_list_user_collections,
    )

    async def _run():
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
                        tools = await session.list_tools()
                        result = await session.call_tool("list_collections", {})
                        return tools, result

    tools, result = asyncio.run(_run())

    assert "list_collections" in {tool.name for tool in tools.tools}
    assert result.structuredContent["total"] == 1
    assert (
        result.structuredContent["collections"][0]["collectionId"]
        == "collection-default"
    )
