"""Dogfood the local MCP RAG server for the meeting absentee question.

Usage:
    set MCP_SERVER_URL=http://localhost:8000/api/mcp
    set MCP_BEARER_TOKEN=<short-lived app JWT>
    python backend/scripts/dogfood_mcp_absentees.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


QUESTION = (
    "List down all the absentees in all meetings in the format of:\n\n"
    "Date: xxx\n"
    "Absentees:\n"
    "1. Name (Reason)\n"
    "2. xxx"
)


def _extract_structured(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def _extract_absentee_blocks(chunks: list[str]) -> str:
    blocks: list[str] = []
    for chunk in chunks:
        date_match = re.search(r"^Date:\s*(.+)$", chunk, flags=re.MULTILINE)
        absentee_match = re.search(
            r"Absentees:\s*(.+?)(?:\n\s*\n|$)",
            chunk,
            flags=re.DOTALL,
        )
        if not date_match or not absentee_match:
            continue
        names: list[str] = []
        for raw_line in absentee_match.group(1).splitlines():
            line = raw_line.strip().lstrip("-").strip()
            if not line:
                continue
            names.append(re.sub(r"^\d+[\).]\s*", "", line))
        if not names:
            continue
        lines = [f"Date: {date_match.group(1).strip()}", "Absentees:"]
        lines.extend(f"{index}. {name}" for index, name in enumerate(names, start=1))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def main() -> None:
    server_url = os.getenv("MCP_SERVER_URL", "http://localhost:8000/api/mcp")
    bearer_token = os.getenv("MCP_BEARER_TOKEN", "").strip()
    if not bearer_token:
        raise RuntimeError("MCP_BEARER_TOKEN must contain a short-lived app JWT.")

    trace: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {bearer_token}"},
        timeout=60,
    ) as http_client:
        async with streamable_http_client(server_url, http_client=http_client) as (
            read,
            write,
            _get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
                    result = await session.call_tool(name, arguments or {})
                    structured = _extract_structured(result)
                    trace.append({"tool": name, "arguments": arguments or {}, "result": structured})
                    return structured

                await call_tool("list_collections")
                search_result = await call_tool(
                    "search_materials",
                    {
                        "query": "meeting minutes attendance absentees reasons",
                        "searchScope": "all_collections",
                        "topK": 12,
                    },
                )

                chunks: list[str] = []
                for item in search_result.get("evidence", []):
                    chunk_result = await call_tool(
                        "fetch_parent_chunk",
                        {
                            "parentId": item.get("parentId"),
                            "collectionId": item.get("collectionId"),
                        },
                    )
                    parent_chunk = chunk_result.get("parentChunk")
                    if isinstance(parent_chunk, dict) and parent_chunk.get("content"):
                        chunks.append(str(parent_chunk["content"]))

    print("Question:")
    print(QUESTION)
    print("\nAnswer:")
    print(_extract_absentee_blocks(chunks) or "No absentees found in MCP-retrieved context.")
    print("\nMCP Tool Trace:")
    print(json.dumps(trace, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
