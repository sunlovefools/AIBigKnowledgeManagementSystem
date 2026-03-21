import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.agent_v2.nodes import editor_node
from app.service.rag.agent_v2.services import llm_client, vector_search


def _base_state() -> dict:
    return {
        "user_instructions": "Change refund period from 14 to 30 days.",
        "run_id": "run-node6",
        "file_ids": None,
        "intention": "edit",
        "goal": "Update refund period from 14 days to 30 days.",
        "lexical_anchors": ["refund", "14 days"],
        "semantic_anchors": ["refund period"],
        "anchors": ["refund", "14 days", "refund period"],
        "constraint": "None",
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


def test_node6_builds_proposals_deduplicates_refs_and_drops_unchanged(monkeypatch):
    state = _base_state()
    clue_result = {
        "merged_confirmed_parent_chunk_refs": [
            {"file_id": "file-a", "file_name": "policy.md", "parent_chunk_number": 1},
            {"file_id": "file-a", "file_name": "policy.md", "parent_chunk_number": 1},
            {"file_id": "file-a", "file_name": "policy.md", "parent_chunk_number": 2},
        ]
    }

    async def _fake_fetch_parent_chunks_for_file_chunk_numbers(file_id: str, chunk_numbers, **kwargs):
        assert file_id == "file-a"
        assert set(chunk_numbers) == {1, 2}
        return {
            1: {
                "file_id": "file-a",
                "file_name": "policy.md",
                "parent_id": "parent-1",
                "chunk_number": 1,
                "page_content": "Refund period is 14 days.",
            },
            2: {
                "file_id": "file-a",
                "file_name": "policy.md",
                "parent_id": "parent-2",
                "chunk_number": 2,
                "page_content": "Unchanged sentence.",
            },
        }

    async def _fake_call_llm(*args, **kwargs):
        user_message = str(kwargs.get("user_message") or "")
        if "Refund period is 14 days." in user_message:
            return (
                "Refund period is 30 days.",
                {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            )
        return (
            "Unchanged sentence.",
            {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
        )

    monkeypatch.setattr(vector_search, "_fetch_parent_chunks_for_file_chunk_numbers", _fake_fetch_parent_chunks_for_file_chunk_numbers)
    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)

    node_result, usage_totals, llm_calls, proposals = asyncio.run(
        editor_node.run_editor_batch(
            state,
            clue_chunk_explorer_result=clue_result,
            batch_id=2,
        )
    )

    assert llm_calls == 2
    assert usage_totals["total_tokens"] == 10
    assert node_result["batch_id"] == 2
    assert node_result["run_summary"]["confirmed_parent_chunk_ref_count"] == 2
    assert node_result["run_summary"]["resolved_parent_chunk_count"] == 2
    assert node_result["run_summary"]["edited_parent_chunk_count"] == 1
    assert node_result["run_summary"]["unchanged_parent_chunk_count"] == 1
    assert len(proposals) == 1
    assert proposals[0]["fileId"] == "file-a"
    assert proposals[0]["parentId"] == "parent-1"
    assert proposals[0]["source"] == "agent"


def test_node6_handles_missing_parent_chunks_without_llm_calls(monkeypatch):
    state = _base_state()
    clue_result = {
        "merged_confirmed_parent_chunk_refs": [
            {"file_id": "file-missing", "file_name": "missing.md", "parent_chunk_number": 9},
        ]
    }

    async def _fake_fetch_parent_chunks_for_file_chunk_numbers(file_id: str, chunk_numbers, **kwargs):
        return {}

    monkeypatch.setattr(vector_search, "_fetch_parent_chunks_for_file_chunk_numbers", _fake_fetch_parent_chunks_for_file_chunk_numbers)

    node_result, usage_totals, llm_calls, proposals = asyncio.run(
        editor_node.run_editor_batch(
            state,
            clue_chunk_explorer_result=clue_result,
            batch_id=1,
        )
    )

    assert llm_calls == 0
    assert usage_totals == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert proposals == []
    assert node_result["run_summary"]["confirmed_parent_chunk_ref_count"] == 1
    assert node_result["run_summary"]["resolved_parent_chunk_count"] == 0
    assert node_result["run_summary"]["error_count"] == 1
    assert isinstance(node_result["errors"], list)


def test_node6_wrapper_returns_intention_proposals_and_usage(monkeypatch):
    state = _base_state()
    state["token_prompt_total"] = 7
    state["token_completion_total"] = 4
    state["token_total"] = 11
    state["llm_call_count"] = 2
    state["node5_clue_chunk_explorer_result"] = {
        "merged_confirmed_parent_chunk_refs": [
            {"file_id": "file-a", "file_name": "policy.md", "parent_chunk_number": 1},
        ]
    }

    async def _fake_fetch_parent_chunks_for_file_chunk_numbers(file_id: str, chunk_numbers, **kwargs):
        return {
            1: {
                "file_id": "file-a",
                "file_name": "policy.md",
                "parent_id": "parent-1",
                "chunk_number": 1,
                "page_content": "Refund period is 14 days.",
            }
        }

    async def _fake_call_llm(*args, **kwargs):
        return (
            "Refund period is 30 days.",
            {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        )

    monkeypatch.setattr(vector_search, "_fetch_parent_chunks_for_file_chunk_numbers", _fake_fetch_parent_chunks_for_file_chunk_numbers)
    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)
    monkeypatch.setattr(editor_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(editor_node.editor_node(state))

    assert result["intention"] == "edit"
    assert len(result["proposals"]) == 1
    assert result["node6_editor_result"]["run_summary"]["edited_parent_chunk_count"] == 1
    assert result["token_prompt_total"] == 12
    assert result["token_completion_total"] == 7
    assert result["token_total"] == 19
    assert result["llm_call_count"] == 3
