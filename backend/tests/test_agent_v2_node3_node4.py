import asyncio
import sys
from pathlib import Path

from langchain_core.documents import Document

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.agent_v2.nodes import (
    file_filtering_node,
    non_strong_signal_file_context_expansion_node,
)
from app.service.rag.agent_v2.services import llm_client, vector_search


def _base_state() -> dict:
    return {
        "user_instructions": "update policy",
        "run_id": "run-node34",
        "goal": "Update policy.",
        "lexical_anchors": [],
        "semantic_anchors": ["anchor one", "anchor two"],
        "anchors": [],
        "constraint": "Only policy section.",
        "node2_search_group_result": {},
        "node3_non_strong_signal_file_context_expansion_result": {},
        "node4_file_filtering_result": {},
        "token_prompt_total": 0,
        "token_completion_total": 0,
        "token_total": 0,
        "llm_call_count": 0,
        "error": None,
        "_session": None,
    }


def test_node3_expands_only_non_strong_files_and_builds_parent_payload(monkeypatch):
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

    async def _fake_fetch_parent_chunks(parent_ids):
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

    assert len(node3["files"]) == 1
    expanded_file = node3["files"][0]
    assert expanded_file["file_id"] == "file-weak"
    assert expanded_file["parent_chunks"][0]["chunk_number"] == 3
    assert "chunk_number: 3" in expanded_file["parent_chunks_prompt_payload"]
    assert "file_name:" not in expanded_file["parent_chunks_prompt_payload"]
    assert all(item[1] == "file-weak" for item in calls)
    assert all(item[2] == 10 for item in calls)


def test_node3_returns_empty_when_no_non_strong_candidates(monkeypatch):
    state = _base_state()
    state["node2_search_group_result"] = {
        "files": [
            {"file_id": "file-strong", "file_name": "strong.md", "strong_signal_file": True},
        ]
    }
    monkeypatch.setattr(non_strong_signal_file_context_expansion_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(non_strong_signal_file_context_expansion_node.non_strong_signal_file_context_expansion_node(state))
    node3 = result["node3_non_strong_signal_file_context_expansion_result"]

    assert node3["files"] == []
    assert node3["run_summary"]["expanded_file_count"] == 0


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

    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)
    monkeypatch.setattr(file_filtering_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(file_filtering_node.file_filtering_node(state))
    node4 = result["node4_file_filtering_result"]
    promoted_ids = {item["file_id"] for item in node4["promoted_files"]}
    dropped_ids = {item["file_id"] for item in node4["dropped_files"]}

    assert promoted_ids == {"file-direct", "file-p75"}
    assert dropped_ids == {"file-p69"}

    evaluations = {item["file_id"]: item for item in node4["evaluations"]}
    assert evaluations["file-direct"]["confidence"] == 1.0
    assert evaluations["file-direct"]["suggested_chunk_numbers"] == []
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
