import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.agentic_modification.nodes import parent_chunk_constraint_verifier_node
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


def test_node5_verifier_confirms_candidate_and_deduplicates_refs(monkeypatch):
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
                "potential_parent_chunks": [10],
            },
        ]
    }

    async def _fake_get_parent_chunks_for_file_range(file_id: str, start_chunk_number: int, end_chunk_number: int, **kwargs):
        if file_id != "file-a":
            return None
        if start_chunk_number == end_chunk_number and start_chunk_number in {10, 11}:
            return [
                {
                    "parent_id": f"parent-{start_chunk_number}",
                    "chunk_number": start_chunk_number,
                    "page_content": f"chunk-{start_chunk_number}",
                    "file_id": file_id,
                    "file_name": "a.md",
                }
            ]
        return None

    async def _fake_get_surrounding_parent_chunks_for_file(file_id: str, chunk_number: int, **kwargs):
        return None

    async def _fake_call_llm(*args, **kwargs):
        messages = kwargs.get("messages", [])
        payload_text = "\n".join(str(item.get("content") or "") for item in messages if isinstance(item, dict))
        if "Candidate parent chunk number:\n10" in payload_text:
            return (
                '{"is_confirmed": true, "reasoning_summary": "chunk 10 is in-scope"}',
                {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            )
        return (
            '{"is_confirmed": false, "reasoning_summary": "chunk 11 is out-of-scope"}',
            {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
        )

    monkeypatch.setattr(vector_search, "_get_parent_chunks_for_file_range", _fake_get_parent_chunks_for_file_range)
    monkeypatch.setattr(
        vector_search,
        "_get_surrounding_parent_chunks_for_file",
        _fake_get_surrounding_parent_chunks_for_file,
    )
    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)

    node_result, usage_totals, llm_calls = asyncio.run(
        parent_chunk_constraint_verifier_node.run_parent_chunk_constraint_verifier_batch(
            state,
            file_filtering_result=filtering_result,
            batch_id=3,
        )
    )

    assert node_result["batch_id"] == 3
    assert node_result["run_summary"]["verification_count"] == 2
    assert node_result["run_summary"]["confirmed_verification_count"] == 1
    assert node_result["run_summary"]["rejected_verification_count"] == 1
    assert node_result["run_summary"]["confirmed_parent_chunk_ref_count"] == 1
    assert usage_totals["total_tokens"] == 11
    assert llm_calls == 2
    assert node_result["merged_confirmed_parent_chunk_refs"] == [
        {
            "file_id": "file-a",
            "file_name": "a.md",
            "parent_chunk_number": 10,
        }
    ]


def test_node5_verifier_rejects_unclear_constraint(monkeypatch):
    state = _base_state()
    filtering_result = {
        "evaluations": [
            {
                "file_id": "file-a",
                "file_name": "a.md",
                "potential_parent_chunks": [8],
            },
        ]
    }

    async def _fake_get_parent_chunks_for_file_range(file_id: str, start_chunk_number: int, end_chunk_number: int, **kwargs):
        return [
            {
                "parent_id": "parent-8",
                "chunk_number": 8,
                "page_content": "candidate chunk",
                "file_id": "file-a",
                "file_name": "a.md",
            }
        ]

    async def _fake_get_surrounding_parent_chunks_for_file(file_id: str, chunk_number: int, **kwargs):
        return None

    async def _fake_call_llm(*args, **kwargs):
        return (
            '{"is_confirmed": false, "reasoning_summary": "constraint scope is unclear"}',
            {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        )

    monkeypatch.setattr(vector_search, "_get_parent_chunks_for_file_range", _fake_get_parent_chunks_for_file_range)
    monkeypatch.setattr(
        vector_search,
        "_get_surrounding_parent_chunks_for_file",
        _fake_get_surrounding_parent_chunks_for_file,
    )
    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)

    node_result, _, _ = asyncio.run(
        parent_chunk_constraint_verifier_node.run_parent_chunk_constraint_verifier_batch(
            state,
            file_filtering_result=filtering_result,
            batch_id=4,
        )
    )

    assert node_result["merged_confirmed_parent_chunk_refs"] == []
    assert node_result["run_summary"]["confirmed_verification_count"] == 0
    verification = node_result["verifications"][0]
    assert verification["candidate_parent_chunk_number"] == 8
    assert verification["is_confirmed"] is False


def test_node5_verifier_rejects_malformed_or_overreaching_output(monkeypatch):
    state = _base_state()
    filtering_result = {
        "evaluations": [
            {
                "file_id": "file-a",
                "file_name": "a.md",
                "potential_parent_chunks": [9],
            },
        ]
    }

    async def _fake_get_parent_chunks_for_file_range(file_id: str, start_chunk_number: int, end_chunk_number: int, **kwargs):
        return [
            {
                "parent_id": "parent-9",
                "chunk_number": 9,
                "page_content": "candidate chunk",
                "file_id": "file-a",
                "file_name": "a.md",
            }
        ]

    async def _fake_get_surrounding_parent_chunks_for_file(file_id: str, chunk_number: int, **kwargs):
        return None

    async def _fake_call_llm(*args, **kwargs):
        return (
            '{"confirmed_parent_chunk_numbers":[99],"clue_outcome":"confirmed","reasoning_summary":"invalid schema"}',
            {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        )

    monkeypatch.setattr(vector_search, "_get_parent_chunks_for_file_range", _fake_get_parent_chunks_for_file_range)
    monkeypatch.setattr(
        vector_search,
        "_get_surrounding_parent_chunks_for_file",
        _fake_get_surrounding_parent_chunks_for_file,
    )
    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)

    node_result, _, _ = asyncio.run(
        parent_chunk_constraint_verifier_node.run_parent_chunk_constraint_verifier_batch(
            state,
            file_filtering_result=filtering_result,
            batch_id=5,
        )
    )

    verification = node_result["verifications"][0]
    assert verification["candidate_parent_chunk_number"] == 9
    assert verification["is_confirmed"] is False
    assert node_result["merged_confirmed_parent_chunk_refs"] == []


def test_node5_verifier_handles_missing_candidate_chunk_safely(monkeypatch):
    state = _base_state()
    filtering_result = {
        "evaluations": [
            {
                "file_id": "file-a",
                "file_name": "a.md",
                "potential_parent_chunks": [42],
            },
        ]
    }

    async def _fake_get_parent_chunks_for_file_range(file_id: str, start_chunk_number: int, end_chunk_number: int, **kwargs):
        return None

    monkeypatch.setattr(vector_search, "_get_parent_chunks_for_file_range", _fake_get_parent_chunks_for_file_range)

    node_result, usage_totals, llm_calls = asyncio.run(
        parent_chunk_constraint_verifier_node.run_parent_chunk_constraint_verifier_batch(
            state,
            file_filtering_result=filtering_result,
            batch_id=6,
        )
    )

    verification = node_result["verifications"][0]
    assert verification["candidate_chunk_found"] is False
    assert verification["is_confirmed"] is False
    assert usage_totals == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert llm_calls == 0
    assert node_result["merged_confirmed_parent_chunk_refs"] == []


def test_node5_verifier_uses_message_mode_tool_history_regression(monkeypatch):
    state = _base_state()
    filtering_result = {
        "evaluations": [
            {
                "file_id": "file-a",
                "file_name": "a.md",
                "potential_parent_chunks": [7],
            },
        ]
    }
    captured = {"messages": None}

    async def _fake_get_parent_chunks_for_file_range(file_id: str, start_chunk_number: int, end_chunk_number: int, **kwargs):
        if start_chunk_number == end_chunk_number == 7:
            return [
                {
                    "parent_id": "parent-7",
                    "chunk_number": 7,
                    "page_content": "candidate chunk",
                    "file_id": "file-a",
                    "file_name": "a.md",
                }
            ]
        return None

    async def _fake_get_surrounding_parent_chunks_for_file(file_id: str, chunk_number: int, **kwargs):
        return None

    async def _fake_call_llm(*args, **kwargs):
        captured["messages"] = kwargs.get("messages")
        assert "user_message" not in kwargs or kwargs.get("user_message") is None
        return (
            '{"is_confirmed": true, "reasoning_summary": "confirmed"}',
            {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        )

    monkeypatch.setattr(vector_search, "_get_parent_chunks_for_file_range", _fake_get_parent_chunks_for_file_range)
    monkeypatch.setattr(
        vector_search,
        "_get_surrounding_parent_chunks_for_file",
        _fake_get_surrounding_parent_chunks_for_file,
    )
    monkeypatch.setattr(llm_client, "_call_llm", _fake_call_llm)

    asyncio.run(
        parent_chunk_constraint_verifier_node.run_parent_chunk_constraint_verifier_batch(
            state,
            file_filtering_result=filtering_result,
            batch_id=7,
        )
    )

    assert isinstance(captured["messages"], list)
    assert len(captured["messages"]) == 3
    user_prompt_message = str(captured["messages"][1].get("content") or "")
    tool_history_message = str(captured["messages"][2].get("content") or "")
    assert "Tool history:" not in user_prompt_message
    assert "Tool history (JSON):" in tool_history_message
