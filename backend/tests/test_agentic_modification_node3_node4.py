import asyncio
import re
import sys
from pathlib import Path

from langchain_core.documents import Document

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.agentic_modification.nodes import (  # noqa: E402
    file_filtering_node,
    non_strong_signal_file_context_expansion_node,
)
from app.service.rag.agentic_modification.services import llm_client, vector_search  # noqa: E402


def _base_state() -> dict:
    return {
        "user_instructions": "update policy",
        "user_id": "user-1",
        "run_id": "run-node34",
        "file_ids": None,
        "intention": "edit",
        "goal": "Update policy.",
        "lexical_anchors": [],
        "semantic_anchors": ["anchor one", "anchor two"],
        "anchors": [],
        "constraint": "Only policy section.",
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


def test_node3_expands_all_candidate_files_and_builds_parent_payload(monkeypatch):
    state = _base_state()
    state["node2_search_group_result"] = {
        "files": [
            {"file_id": "file-strong", "file_name": "strong.md", "strong_signal_file": True},
            {"file_id": "file-weak", "file_name": "weak.md", "strong_signal_file": False},
        ]
    }

    calls: list[tuple[str, str, int]] = []

    async def _fake_run_semantic_search_for_file(query: str, file_id: str, top_k: int, **kwargs):
        calls.append((query, file_id, top_k))
        doc = Document(
            page_content="child",
            metadata={
                "child_chunk_id": f"{file_id}-{query}-child",
                "file_metadata": {"file_id": file_id, "file_name": "weak.md"},
                "child_chunk_metadata": {"parent_id": "parent-1"},
            },
        )
        return [(doc, 0.75)], "native_filter"

    async def _fake_fetch_parent_chunks(parent_ids, **kwargs):
        assert parent_ids == ["parent-1"]
        return [
            {
                "page_content": "Parent content",
                "metadata": {
                    "file_metadata": {"file_id": "file-weak", "file_name": "weak.md"},
                    "parent_chunk_metadata": {
                        "parent_chunk_number": 3,
                        "child_chunks_ids": ["child-a", "child-b"],
                    },
                },
            }
        ]

    monkeypatch.setattr(
        vector_search,
        "_run_semantic_search_for_file",
        _fake_run_semantic_search_for_file,
    )
    monkeypatch.setattr(
        non_strong_signal_file_context_expansion_node,
        "log_modification_agent_search_group",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(vector_search, "_fetch_parent_chunks", _fake_fetch_parent_chunks)

    result = asyncio.run(
        non_strong_signal_file_context_expansion_node.non_strong_signal_file_context_expansion_node(state)
    )
    node3 = result["node3_non_strong_signal_file_context_expansion_result"]

    assert len(node3["files"]) == 2
    files_by_id = {item["file_id"]: item for item in node3["files"]}
    assert set(files_by_id.keys()) == {"file-strong", "file-weak"}
    assert files_by_id["file-weak"]["parent_chunks"][0]["chunk_number"] == 3
    assert files_by_id["file-weak"]["parent_chunks"][0]["child_chunk_ids"] == ["child-a", "child-b"]
    assert "chunk_number: 3" in files_by_id["file-weak"]["parent_chunks_prompt_payload"]
    assert "file_name:" not in files_by_id["file-weak"]["parent_chunks_prompt_payload"]
    assert {item[1] for item in calls} == {"file-strong", "file-weak"}
    assert all(item[2] == 10 for item in calls)


def test_node3_processes_strong_only_candidates(monkeypatch):
    state = _base_state()
    state["node2_search_group_result"] = {
        "files": [
            {"file_id": "file-strong", "file_name": "strong.md", "strong_signal_file": True},
        ]
    }

    async def _fake_run_semantic_search_for_file(query: str, file_id: str, top_k: int, **kwargs):
        doc = Document(
            page_content="child",
            metadata={
                "child_chunk_id": f"{file_id}-{query}-child",
                "file_metadata": {"file_id": file_id, "file_name": "strong.md"},
                "child_chunk_metadata": {"parent_id": "parent-1"},
            },
        )
        return [(doc, 0.75)], "native_filter"

    async def _fake_fetch_parent_chunks(parent_ids, **kwargs):
        return [
            {
                "page_content": "Parent content",
                "metadata": {
                    "file_metadata": {"file_id": "file-strong", "file_name": "strong.md"},
                    "parent_chunk_metadata": {"parent_chunk_number": 3},
                },
            }
        ]

    monkeypatch.setattr(
        vector_search,
        "_run_semantic_search_for_file",
        _fake_run_semantic_search_for_file,
    )
    monkeypatch.setattr(vector_search, "_fetch_parent_chunks", _fake_fetch_parent_chunks)
    monkeypatch.setattr(
        non_strong_signal_file_context_expansion_node,
        "log_modification_agent_search_group",
        lambda **kwargs: None,
    )

    result = asyncio.run(
        non_strong_signal_file_context_expansion_node.non_strong_signal_file_context_expansion_node(state)
    )
    node3 = result["node3_non_strong_signal_file_context_expansion_result"]

    assert len(node3["files"]) == 1
    assert node3["files"][0]["file_id"] == "file-strong"
    assert node3["run_summary"]["expanded_file_count"] == 1


def test_node4_classifies_confirmed_and_potential_and_builds_refs(monkeypatch):
    state = _base_state()
    state["token_prompt_total"] = 5
    state["token_completion_total"] = 7
    state["token_total"] = 12
    state["llm_call_count"] = 1
    state["node2_search_group_result"] = {"files": []}
    state["node3_non_strong_signal_file_context_expansion_result"] = {
        "files": [
            {
                "file_id": "file-a",
                "file_name": "a.md",
                "parent_chunks": [
                    {"chunk_number": 2, "page_content": "A", "child_chunk_ids": ["child-2"]},
                    {"chunk_number": 3, "page_content": "B", "child_chunk_ids": ["child-3"]},
                    {"chunk_number": 4, "page_content": "C", "child_chunk_ids": ["child-4"]},
                ],
            }
        ]
    }

    async def _fake_call_llm(*args, **kwargs):
        return (
            '{"confirmed_parent_chunks":[2],"potential_parent_chunks":[3,4],"reasoning_summary":"exact + nearby"}',
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)
    monkeypatch.setattr(file_filtering_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(file_filtering_node.file_filtering_node(state))
    node4 = result["node4_file_filtering_result"]
    evaluation = node4["evaluations"][0]

    assert evaluation["confirmed_parent_chunks"] == [2]
    assert evaluation["potential_parent_chunks"] == [3, 4]
    assert node4["run_summary"]["evaluated_file_count"] == 1
    assert node4["run_summary"]["confirmed_parent_chunk_ref_count"] == 1
    assert node4["run_summary"]["potential_parent_chunk_ref_count"] == 2
    assert node4["potential_file_ids"] == ["file-a"]
    assert node4["merged_confirmed_parent_chunk_refs"] == [
        {"file_id": "file-a", "file_name": "a.md", "parent_chunk_number": 2}
    ]
    assert node4["merged_potential_parent_chunk_refs"] == [
        {"file_id": "file-a", "file_name": "a.md", "parent_chunk_number": 3},
        {"file_id": "file-a", "file_name": "a.md", "parent_chunk_number": 4},
    ]
    assert node4["excluded_child_chunk_ids_by_file"] == [
        {"file_id": "file-a", "child_chunk_ids": ["child-2", "child-3", "child-4"]}
    ]
    assert result["token_prompt_total"] == 15
    assert result["token_completion_total"] == 12
    assert result["token_total"] == 27
    assert result["llm_call_count"] == 2


def test_node4_malformed_output_falls_back_to_empty_lists(monkeypatch):
    state = _base_state()
    state["node2_search_group_result"] = {"files": []}
    state["node3_non_strong_signal_file_context_expansion_result"] = {
        "files": [
            {
                "file_id": "file-a",
                "file_name": "a.md",
                "parent_chunks": [{"chunk_number": 5, "page_content": "content"}],
            }
        ]
    }

    async def _fake_call_llm(*args, **kwargs):
        return "not-json", {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}

    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)
    monkeypatch.setattr(file_filtering_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(file_filtering_node.file_filtering_node(state))
    node4 = result["node4_file_filtering_result"]
    evaluation = node4["evaluations"][0]

    assert evaluation["confirmed_parent_chunks"] == []
    assert evaluation["potential_parent_chunks"] == []
    assert node4["merged_confirmed_parent_chunk_refs"] == []
    assert node4["merged_potential_parent_chunk_refs"] == []


def test_node4_evaluates_files_in_parallel_and_preserves_order(monkeypatch):
    state = _base_state()
    state["node2_search_group_result"] = {"files": []}
    state["node3_non_strong_signal_file_context_expansion_result"] = {
        "files": [
            {
                "file_id": "file-a",
                "file_name": "a.md",
                "parent_chunks": [{"chunk_number": 11, "page_content": "A"}],
            },
            {
                "file_id": "file-b",
                "file_name": "b.md",
                "parent_chunks": [{"chunk_number": 12, "page_content": "B"}],
            },
            {
                "file_id": "file-c",
                "file_name": "c.md",
                "parent_chunks": [{"chunk_number": 13, "page_content": "C"}],
            },
        ]
    }

    started_files: set[str] = set()
    all_started = asyncio.Event()

    def _resolve_file_id(user_message: str) -> str:
        matches = re.findall(r"chunk_number:\s*(\d+)", user_message)
        if not matches:
            raise AssertionError(f"Unexpected prompt payload: {user_message}")
        chunk_number = int(matches[-1])
        if chunk_number == 11:
            return "file-a"
        if chunk_number == 12:
            return "file-b"
        if chunk_number == 13:
            return "file-c"
        raise AssertionError(f"Unexpected prompt payload: {user_message}")

    async def _fake_call_llm(*args, **kwargs):
        user_message = str(kwargs.get("user_message") or "")
        file_id = _resolve_file_id(user_message)
        started_files.add(file_id)
        if len(started_files) == 3:
            all_started.set()
        await all_started.wait()
        potential_number = {"file-a": 11, "file-b": 12, "file-c": 13}[file_id]
        return (
            f'{{"confirmed_parent_chunks":[],"potential_parent_chunks":[{potential_number}],"reasoning_summary":"need exploration"}}',
            {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
        )

    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)
    monkeypatch.setattr(file_filtering_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(asyncio.wait_for(file_filtering_node.file_filtering_node(state), timeout=2))
    node4 = result["node4_file_filtering_result"]

    assert started_files == {"file-a", "file-b", "file-c"}
    assert [item["file_id"] for item in node4["evaluations"]] == ["file-a", "file-b", "file-c"]
    assert node4["run_summary"]["evaluated_file_count"] == 3
    assert node4["run_summary"]["potential_file_count"] == 3
    assert result["llm_call_count"] == 3
    assert result["token_total"] == 3
