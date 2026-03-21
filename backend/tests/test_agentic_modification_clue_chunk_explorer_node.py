import asyncio
import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.agentic_modification.nodes import clue_chunk_explorer_node
from app.service.rag.agentic_modification.services import llm_client, vector_search


def _base_state() -> dict:
    return {
        "user_instructions": "update policy",
        "run_id": "run-node5",
        "file_ids": None,
        "intention": "edit",
        "goal": "Update policy.",
        "lexical_anchors": ["policy"],
        "semantic_anchors": ["policy rule"],
        "anchors": [],
        "constraint": "Only policy section.",
        "node2_search_group_result": {},
        "node3_non_strong_signal_file_context_expansion_result": {},
        "node4_file_filtering_result": {},
        "node5_clue_chunk_explorer_result": {},
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


def _fake_parent_row(file_id: str, file_name: str, chunk_number: int, parent_id: str) -> dict:
    return {
        "_id": parent_id,
        "value": {
            "page_content": f"content-{chunk_number}",
            "metadata": {
                "file_metadata": {
                    "file_id": file_id,
                    "file_name": file_name,
                },
                "parent_chunk_metadata": {
                    "parent_chunk_number": chunk_number,
                },
            },
        },
    }


def test_vector_search_range_tools_partial_and_cache_hydration(monkeypatch):
    class _FakeCollection:
        def __init__(self):
            self.find_call_count = 0

        def find(self, filter_doc, projection=None):
            self.find_call_count += 1
            file_id = filter_doc.get("value.metadata.file_metadata.file_id")
            chunk_numbers = (
                filter_doc.get("value.metadata.parent_chunk_metadata.parent_chunk_number", {})
                .get("$in", [])
            )
            if file_id != "file-a":
                return []
            rows = []
            for number in chunk_numbers:
                if number in {1, 2}:
                    rows.append(_fake_parent_row("file-a", "a.md", number, f"parent-{number}"))
            return rows

    fake_collection = _FakeCollection()
    fake_vectordb_module = types.SimpleNamespace(
        PARENT_STORE=types.SimpleNamespace(collection=fake_collection)
    )
    vectordb_pkg = importlib.import_module("app.vectordb")
    monkeypatch.setitem(sys.modules, "app.vectordb.vectordb", fake_vectordb_module)
    monkeypatch.setattr(vectordb_pkg, "vectordb", fake_vectordb_module, raising=False)

    cache = vector_search._ensure_retrieval_cache({})

    first = asyncio.run(
        vector_search._get_parent_chunks_for_file_range(
            file_id="file-a",
            start_chunk_number=1,
            end_chunk_number=3,
            cache=cache,
        )
    )
    assert [item["chunk_number"] for item in first] == [1, 2]
    assert fake_collection.find_call_count == 1

    second = asyncio.run(
        vector_search._get_parent_chunks_for_file_range(
            file_id="file-a",
            start_chunk_number=1,
            end_chunk_number=2,
            cache=cache,
        )
    )
    assert [item["chunk_number"] for item in second] == [1, 2]
    assert fake_collection.find_call_count == 1

    full_miss = asyncio.run(
        vector_search._get_parent_chunks_for_file_range(
            file_id="file-a",
            start_chunk_number=4,
            end_chunk_number=5,
            cache=cache,
        )
    )
    assert full_miss is None
    assert fake_collection.find_call_count == 2

    cross_file = asyncio.run(
        vector_search._get_parent_chunks_for_file_range(
            file_id="file-b",
            start_chunk_number=1,
            end_chunk_number=2,
            cache=cache,
        )
    )
    assert cross_file is None
    assert fake_collection.find_call_count == 3


def test_node5_explores_clues_concurrently_and_deduplicates_confirmed_chunks(monkeypatch):
    state = _base_state()
    filtering_result = {
        "evaluations": [
            {
                "file_id": "file-a",
                "file_name": "a.md",
                "potential_parent_chunks": [10, 11],
            },
            {
                "file_id": "file-a",
                "file_name": "a.md",
                "potential_parent_chunks": [11],
            },
            {
                "file_id": "file-b",
                "file_name": "b.md",
                "potential_parent_chunks": [],
            },
        ]
    }

    async def _fake_get_parent_chunks_for_file_range(file_id: str, start_chunk_number: int, end_chunk_number: int, **kwargs):
        if file_id != "file-a":
            return None
        if start_chunk_number == end_chunk_number and start_chunk_number in {10, 11}:
            return [
                {
                    "parent_id": f"origin-{start_chunk_number}",
                    "chunk_number": start_chunk_number,
                    "page_content": f"origin-{start_chunk_number}",
                    "file_id": file_id,
                    "file_name": "a.md",
                }
            ]
        return None

    async def _fake_get_surrounding_parent_chunks_for_file(file_id: str, chunk_number: int, **kwargs):
        return None

    async def _fake_fetch_parent_chunks_for_file_chunk_numbers(file_id: str, chunk_numbers, **kwargs):
        if file_id != "file-a":
            return {}
        if 12 in set(chunk_numbers):
            return {
                12: {
                    "parent_id": "parent-12",
                    "chunk_number": 12,
                    "page_content": "target",
                    "file_id": file_id,
                    "file_name": "a.md",
                }
            }
        return {}

    call_state = {"active": 0, "max_active": 0}

    async def _fake_call_llm(*args, **kwargs):
        user_message = str(kwargs.get("user_message") or "")
        call_state["active"] += 1
        call_state["max_active"] = max(call_state["max_active"], call_state["active"])
        await asyncio.sleep(0.05)
        call_state["active"] -= 1
        if "Origin clue chunk number:\n10" in user_message:
            return (
                '{"confirmed_parent_chunk_numbers":[12],"clue_outcome":"confirmed","reasoning_summary":"from clue 10"}',
                {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            )
        return (
            '{"confirmed_parent_chunk_numbers":[12],"clue_outcome":"confirmed","reasoning_summary":"from clue 11"}',
            {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
        )

    monkeypatch.setattr(vector_search, "_get_parent_chunks_for_file_range", _fake_get_parent_chunks_for_file_range)
    monkeypatch.setattr(
        vector_search,
        "_get_surrounding_parent_chunks_for_file",
        _fake_get_surrounding_parent_chunks_for_file,
    )
    monkeypatch.setattr(
        vector_search,
        "_fetch_parent_chunks_for_file_chunk_numbers",
        _fake_fetch_parent_chunks_for_file_chunk_numbers,
    )
    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)

    node_result, usage_totals, llm_calls = asyncio.run(
        clue_chunk_explorer_node.run_clue_chunk_explorer_batch(
            state,
            file_filtering_result=filtering_result,
            batch_id=3,
        )
    )

    assert node_result["batch_id"] == 3
    assert node_result["run_summary"]["exploration_count"] == 2
    assert node_result["run_summary"]["confirmed_parent_chunk_ref_count"] == 1
    assert node_result["run_summary"]["confirmed_file_count"] == 1
    assert llm_calls == 2
    assert usage_totals["total_tokens"] == 11
    assert call_state["max_active"] >= 2

    merged = node_result["merged_confirmed_parent_chunk_refs"]
    assert merged == [
        {
            "file_id": "file-a",
            "file_name": "a.md",
            "parent_chunk_number": 12,
        }
    ]


def test_node5_tool_loop_bridge_and_dead_end_normalization(monkeypatch):
    state = _base_state()
    filtering_result = {
        "evaluations": [
            {
                "file_id": "file-a",
                "file_name": "a.md",
                "potential_parent_chunks": [4],
            },
            {
                "file_id": "file-a",
                "file_name": "a.md",
                "potential_parent_chunks": [8],
            },
        ]
    }

    async def _fake_get_parent_chunks_for_file_range(file_id: str, start_chunk_number: int, end_chunk_number: int, **kwargs):
        if start_chunk_number == end_chunk_number and start_chunk_number in {4, 8}:
            return [
                {
                    "parent_id": f"origin-{start_chunk_number}",
                    "chunk_number": start_chunk_number,
                    "page_content": f"origin-{start_chunk_number}",
                    "file_id": "file-a",
                    "file_name": "a.md",
                }
            ]
        return None

    async def _fake_get_surrounding_parent_chunks_for_file(file_id: str, chunk_number: int, **kwargs):
        if chunk_number == 4:
            return [
                {
                    "parent_id": "parent-5",
                    "chunk_number": 5,
                    "page_content": "bridge target",
                    "file_id": "file-a",
                    "file_name": "a.md",
                }
            ]
        return None

    async def _fake_fetch_parent_chunks_for_file_chunk_numbers(file_id: str, chunk_numbers, **kwargs):
        normalized = set(chunk_numbers)
        if 5 in normalized:
            return {
                5: {
                    "parent_id": "parent-5",
                    "chunk_number": 5,
                    "page_content": "confirmed",
                    "file_id": "file-a",
                    "file_name": "a.md",
                }
            }
        return {}

    async def _fake_call_llm(*args, **kwargs):
        user_message = str(kwargs.get("user_message") or "")
        if "Origin clue chunk number:\n4" in user_message and '"tool_name": "get_surrounding_parent_chunks"' not in user_message:
            return (
                '{"action":"tool","tool_name":"get_surrounding_parent_chunks","arguments":{"chunk_number":4}}',
                {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            )
        if "Origin clue chunk number:\n4" in user_message:
            return (
                '{"confirmed_parent_chunk_numbers":[5],"clue_outcome":"confirmed","reasoning_summary":"resolved via nearby chunk"}',
                {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            )
        return (
            '{"confirmed_parent_chunk_numbers":[99],"clue_outcome":"confirmed","reasoning_summary":"invalid target"}',
            {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        )

    monkeypatch.setattr(vector_search, "_get_parent_chunks_for_file_range", _fake_get_parent_chunks_for_file_range)
    monkeypatch.setattr(
        vector_search,
        "_get_surrounding_parent_chunks_for_file",
        _fake_get_surrounding_parent_chunks_for_file,
    )
    monkeypatch.setattr(
        vector_search,
        "_fetch_parent_chunks_for_file_chunk_numbers",
        _fake_fetch_parent_chunks_for_file_chunk_numbers,
    )
    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)

    node_result, usage_totals, llm_calls = asyncio.run(
        clue_chunk_explorer_node.run_clue_chunk_explorer_batch(
            state,
            file_filtering_result=filtering_result,
            batch_id=4,
        )
    )

    assert llm_calls == 3
    assert usage_totals["total_tokens"] == 9
    assert node_result["run_summary"]["exploration_count"] == 2
    assert node_result["run_summary"]["confirmed_exploration_count"] == 1
    assert node_result["run_summary"]["dead_end_count"] == 1

    explorations = sorted(node_result["explorations"], key=lambda item: item["clue_chunk_number"])
    assert explorations[0]["clue_chunk_number"] == 4
    assert explorations[0]["clue_outcome"] == "confirmed"
    assert explorations[0]["confirmed_parent_chunk_numbers"] == [5]
    assert explorations[0]["tool_call_count"] == 1

    assert explorations[1]["clue_chunk_number"] == 8
    assert explorations[1]["clue_outcome"] == "dead_end"
    assert explorations[1]["confirmed_parent_chunk_numbers"] == []
