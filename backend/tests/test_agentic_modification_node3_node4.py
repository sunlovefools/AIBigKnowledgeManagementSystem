import asyncio
import sys
from pathlib import Path

from langchain_core.documents import Document

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.agentic_modification.nodes import (
    file_filtering_node,
    non_strong_signal_file_context_expansion_node,
)
from app.service.rag.agentic_modification.services import llm_client, vector_search


def _base_state() -> dict:
    return {
        "user_instructions": "update policy",
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


def test_node3_expands_all_candidate_files_and_builds_parent_payload(monkeypatch):
    state = _base_state()
    state["node2_search_group_result"] = {
        "files": [
            {"file_id": "file-strong", "file_name": "strong.md", "strong_signal_file": True},
            {"file_id": "file-weak", "file_name": "weak.md", "strong_signal_file": False},
        ]
    }

    calls: list[tuple[str, str, int]] = []

    async def _fake_run_semantic_search_for_file(query: str, file_id: str, top_k: int):
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
                    "parent_chunk_metadata": {"parent_chunk_number": 3},
                },
            }
        ]

    monkeypatch.setattr(
        vector_search,
        "_run_semantic_search_for_file",
        _fake_run_semantic_search_for_file,
    )
    monkeypatch.setattr(non_strong_signal_file_context_expansion_node, "log_modification_agent_search_group", lambda **kwargs: None)

    monkeypatch.setattr(vector_search, "_fetch_parent_chunks", _fake_fetch_parent_chunks)

    result = asyncio.run(non_strong_signal_file_context_expansion_node.non_strong_signal_file_context_expansion_node(state))
    node3 = result["node3_non_strong_signal_file_context_expansion_result"]

    assert len(node3["files"]) == 2
    files_by_id = {item["file_id"]: item for item in node3["files"]}
    assert set(files_by_id.keys()) == {"file-strong", "file-weak"}
    assert files_by_id["file-weak"]["parent_chunks"][0]["chunk_number"] == 3
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
    async def _fake_run_semantic_search_for_file(query: str, file_id: str, top_k: int):
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
    monkeypatch.setattr(non_strong_signal_file_context_expansion_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(non_strong_signal_file_context_expansion_node.non_strong_signal_file_context_expansion_node(state))
    node3 = result["node3_non_strong_signal_file_context_expansion_result"]

    assert len(node3["files"]) == 1
    assert node3["files"][0]["file_id"] == "file-strong"
    assert node3["run_summary"]["expanded_file_count"] == 1


def test_node4_promotes_direct_and_potential_above_threshold(monkeypatch):
    state = _base_state()
    state["token_prompt_total"] = 5
    state["token_completion_total"] = 7
    state["token_total"] = 12
    state["llm_call_count"] = 1
    state["node2_search_group_result"] = {"files": []}
    state["node3_non_strong_signal_file_context_expansion_result"] = {
        "files": [
            {
                "file_id": "file-direct",
                "file_name": "direct.md",
                "parent_chunks": [{"chunk_number": 2, "page_content": "A"}],
                "parent_chunks_prompt_payload": "[1]\nchunk_number: 2\npage_content: \"A\"",
            },
            {
                "file_id": "file-p75",
                "file_name": "p75.md",
                "parent_chunks": [{"chunk_number": 3, "page_content": "B"}],
                "parent_chunks_prompt_payload": "[1]\nchunk_number: 3\npage_content: \"B\"",
            },
            {
                "file_id": "file-p69",
                "file_name": "p69.md",
                "parent_chunks": [{"chunk_number": 4, "page_content": "C"}],
                "parent_chunks_prompt_payload": "[1]\nchunk_number: 4\npage_content: \"C\"",
            },
        ]
    }

    responses = [
        ('{"decision":"direct_match","confidence":0.1,"reasoning_summary":"exact","suggested_chunk_numbers":[2]}', {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
        ('{"decision":"potential_match","confidence":0.75,"reasoning_summary":"maybe","suggested_chunk_numbers":[3]}', {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}),
        ('{"decision":"potential_match","confidence":0.69,"reasoning_summary":"weak","suggested_chunk_numbers":[4]}', {"prompt_tokens": 6, "completion_tokens": 3, "total_tokens": 9}),
    ]

    async def _fake_call_llm(*args, **kwargs):
        return responses.pop(0)

    async def _fake_run_semantic_search_for_file(query: str, file_id: str, top_k: int, **kwargs):
        return [], "native_filter"

    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)
    monkeypatch.setattr(vector_search, "_run_semantic_search_for_file", _fake_run_semantic_search_for_file)
    monkeypatch.setattr(file_filtering_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(file_filtering_node.file_filtering_node(state))
    node4 = result["node4_file_filtering_result"]
    promoted_ids = {item["file_id"] for item in node4["promoted_files"]}
    dropped_ids = {item["file_id"] for item in node4["dropped_files"]}

    assert promoted_ids == {"file-direct", "file-p75"}
    assert dropped_ids == {"file-p69"}

    evaluations = {item["file_id"]: item for item in node4["evaluations"]}
    assert evaluations["file-direct"]["confidence"] == 1.0
    assert evaluations["file-direct"]["suggested_chunk_numbers"] == [2]
    assert evaluations["file-p75"]["suggested_chunk_numbers"] == [3]

    assert result["token_prompt_total"] == 29
    assert result["token_completion_total"] == 19
    assert result["token_total"] == 48
    assert result["llm_call_count"] == 4


def test_node4_rejects_on_malformed_llm_output(monkeypatch):
    state = _base_state()
    state["node2_search_group_result"] = {"files": []}
    state["node3_non_strong_signal_file_context_expansion_result"] = {
        "files": [
            {
                "file_id": "file-a",
                "file_name": "a.md",
                "parent_chunks": [{"chunk_number": 5, "page_content": "content"}],
                "parent_chunks_prompt_payload": "[1]\nchunk_number: 5\npage_content: \"content\"",
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

    assert evaluation["decision"] == "reject"
    assert evaluation["confidence"] == 0.0
    assert evaluation["suggested_chunk_numbers"] == []
    assert node4["promoted_files"] == []


def test_node4_strong_signal_file_is_llm_evaluated(monkeypatch):
    state = _base_state()
    state["node2_search_group_result"] = {
        "files": [
            {"file_id": "file-strong", "file_name": "strong.md", "strong_signal_file": True},
        ]
    }
    state["node3_non_strong_signal_file_context_expansion_result"] = {
        "files": [
            {
                "file_id": "file-strong",
                "file_name": "strong.md",
                "semantic_anchors": ["anchor one"],
                "expanded_child_chunks": [],
                "parent_chunks": [{"parent_id": "parent-1", "chunk_number": 1, "page_content": "x"}],
            }
        ]
    }

    call_counter = {"count": 0}

    async def _fake_call_llm(*args, **kwargs):
        call_counter["count"] += 1
        return (
            '{"decision":"direct_match","confidence":1.0,"reasoning_summary":"exact","suggested_chunk_numbers":[]}',
            {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        )

    async def _fake_run_semantic_search_for_file(query: str, file_id: str, top_k: int, **kwargs):
        return [], "native_filter"

    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)
    monkeypatch.setattr(vector_search, "_run_semantic_search_for_file", _fake_run_semantic_search_for_file)
    monkeypatch.setattr(file_filtering_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(file_filtering_node.file_filtering_node(state))
    node4 = result["node4_file_filtering_result"]

    assert call_counter["count"] == 1
    assert node4["run_summary"]["strong_signal_file_count"] == 1
    assert node4["evaluations"][0]["strong_signal_file"] is True
    assert {item["file_id"] for item in node4["promoted_files"]} == {"file-strong"}


def test_node4_confidence_loop_researches_until_no_new_parents(monkeypatch):
    state = _base_state()
    state["node2_search_group_result"] = {"files": []}
    state["node3_non_strong_signal_file_context_expansion_result"] = {
        "files": [
            {
                "file_id": "file-a",
                "file_name": "a.md",
                "semantic_anchors": ["anchor one"],
                "expanded_child_chunks": [],
                "parent_chunks": [{"parent_id": "parent-1", "chunk_number": 1, "page_content": "base"}],
            }
        ]
    }

    llm_responses = [
        ('{"decision":"potential_match","confidence":0.8,"reasoning_summary":"more context needed","suggested_chunk_numbers":[1]}', {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}),
        ('{"decision":"direct_match","confidence":1.0,"reasoning_summary":"now exact","suggested_chunk_numbers":[]}', {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}),
    ]
    search_calls = {"count": 0}

    async def _fake_call_llm(*args, **kwargs):
        return llm_responses.pop(0)

    async def _fake_run_semantic_search_for_file(query: str, file_id: str, top_k: int, **kwargs):
        search_calls["count"] += 1
        if search_calls["count"] == 1:
            doc = Document(
                page_content="child",
                metadata={
                    "child_chunk_id": "child-new",
                    "file_metadata": {"file_id": file_id, "file_name": "a.md"},
                    "child_chunk_metadata": {"parent_id": "parent-2"},
                },
            )
            return [(doc, 0.8)], "native_filter"
        return [], "native_filter"

    async def _fake_fetch_parent_chunks(parent_ids, **kwargs):
        assert parent_ids == ["parent-2"]
        return [
            {
                "page_content": "new parent context",
                "metadata": {
                    "file_metadata": {"file_id": "file-a", "file_name": "a.md"},
                    "parent_chunk_metadata": {"parent_chunk_number": 2},
                },
            }
        ]

    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)
    monkeypatch.setattr(vector_search, "_run_semantic_search_for_file", _fake_run_semantic_search_for_file)
    monkeypatch.setattr(vector_search, "_fetch_parent_chunks", _fake_fetch_parent_chunks)
    monkeypatch.setattr(file_filtering_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(file_filtering_node.file_filtering_node(state))
    node4 = result["node4_file_filtering_result"]
    evaluation = node4["evaluations"][0]

    assert evaluation["round_count"] == 2
    assert evaluation["exhaustion_reason"] == "no_new_parent_chunks"
    assert evaluation["parent_chunk_count"] == 2
    assert evaluation["promoted"] is True
    assert search_calls["count"] == 2
    assert "cache_stats" in node4["run_summary"]
