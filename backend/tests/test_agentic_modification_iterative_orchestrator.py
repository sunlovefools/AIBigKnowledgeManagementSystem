import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.agentic_modification.nodes import iterative_search_filter_orchestrator_node


def _base_state() -> dict:
    return {
        "user_instructions": "update policy",
        "run_id": "run-iterative",
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


def _search_batch(batch_id: int, file_id: str | None, *, strong: bool) -> dict:
    files = []
    if file_id is not None:
        files.append(
            {
                "file_id": file_id,
                "file_name": f"{file_id}.md",
                "strong_signal_file": strong,
            }
        )
    return {
        "batch_id": batch_id,
        "queries": {"lexical_anchors": ["x"], "semantic_anchors": ["y"]},
        "query_hits": {"lexical": {}, "semantic": {}},
        "children": [],
        "files": files,
        "run_summary": {
            "top_k_per_query": 15,
            "total_hits": 1 if files else 0,
            "total_files": len(files),
            "strong_signal_file_count": sum(1 for item in files if item["strong_signal_file"]),
        },
    }


def _expansion_batch(batch_id: int, file_id: str | None) -> dict:
    files = []
    if file_id is not None:
        files = [
            {
                "file_id": file_id,
                "file_name": f"{file_id}.md",
                "parent_chunks": [{"chunk_number": 1, "page_content": "content"}],
                "parent_chunks_prompt_payload": "[1]\nchunk_number: 1\npage_content: \"content\"",
            }
        ]
    return {
        "batch_id": batch_id,
        "queries": {"semantic_anchors": ["y"]},
        "files": files,
        "run_summary": {
            "expanded_file_count": len(files),
            "expanded_parent_chunk_count": len(files),
        },
    }


def _filter_batch(
    batch_id: int,
    *,
    file_id: str | None,
    potential_chunk_numbers: list[int],
    confirmed_chunk_numbers: list[int] | None = None,
    excluded_child_chunk_ids: list[str] | None = None,
) -> dict:
    confirmed_chunk_numbers = confirmed_chunk_numbers or []
    excluded_child_chunk_ids = excluded_child_chunk_ids or []
    file_name = f"{file_id}.md" if file_id else "unknown.md"
    potential_refs = [
        {"file_id": file_id, "file_name": file_name, "parent_chunk_number": number}
        for number in potential_chunk_numbers
        if file_id
    ]
    confirmed_refs = [
        {"file_id": file_id, "file_name": file_name, "parent_chunk_number": number}
        for number in confirmed_chunk_numbers
        if file_id
    ]
    return {
        "batch_id": batch_id,
        "goal": "Update policy.",
        "constraint": "Only policy section.",
        "strong_signal_files": [],
        "evaluations": [
            {
                "file_id": file_id or "unknown",
                "file_name": file_name,
                "confirmed_parent_chunks": confirmed_chunk_numbers,
                "potential_parent_chunks": potential_chunk_numbers,
                "reasoning_summary": "synthetic",
            }
        ],
        "merged_confirmed_parent_chunk_refs": confirmed_refs,
        "merged_potential_parent_chunk_refs": potential_refs,
        "potential_file_ids": [file_id] if file_id and potential_chunk_numbers else [],
        "excluded_child_chunk_ids_by_file": (
            [{"file_id": file_id, "child_chunk_ids": excluded_child_chunk_ids}]
            if file_id and excluded_child_chunk_ids
            else []
        ),
        "run_summary": {
            "evaluated_file_count": 1,
            "confirmed_file_count": 1 if confirmed_chunk_numbers else 0,
            "potential_file_count": 1 if potential_chunk_numbers else 0,
            "confirmed_parent_chunk_ref_count": len(confirmed_refs),
            "potential_parent_chunk_ref_count": len(potential_refs),
        },
    }


def _verifier_batch(batch_id: int, refs: list[dict] | None = None) -> dict:
    refs = refs or []
    return {
        "batch_id": batch_id,
        "goal": "Update policy.",
        "constraint": "Only policy section.",
        "verifications": [],
        "confirmed_parent_chunks_by_file": [],
        "merged_confirmed_parent_chunk_refs": refs,
        "run_summary": {
            "verification_count": 0,
            "confirmed_verification_count": 0,
            "rejected_verification_count": 0,
            "confirmed_parent_chunk_ref_count": len(refs),
            "tool_call_count": 0,
            "llm_call_count": 0,
        },
    }


def test_orchestrator_loops_on_potential_files_and_forwards_child_exclusions(monkeypatch):
    state = _base_state()
    seen_search_kwargs: list[dict] = []

    async def _fake_search_batch(_state, **kwargs):
        seen_search_kwargs.append(kwargs)
        batch_id = int(kwargs.get("batch_id", 1))
        if batch_id == 1:
            return _search_batch(1, "file-a", strong=True)
        if batch_id == 2:
            assert kwargs.get("allowed_file_ids_override") == {"file-a"}
            assert kwargs.get("excluded_child_chunk_ids_by_file") == {"file-a": {"child-1", "child-2"}}
            return _search_batch(2, None, strong=False)
        return _search_batch(batch_id, None, strong=False)

    async def _fake_expand_batch(_state, *, search_group_result, batch_id):
        files = search_group_result.get("files", [])
        file_id = files[0]["file_id"] if files else None
        return _expansion_batch(batch_id, file_id)

    async def _fake_filter_batch(_state, *, search_group_result, expansion_result, batch_id):
        files = search_group_result.get("files", [])
        file_id = files[0]["file_id"] if files else None
        if batch_id == 1:
            return _filter_batch(
                batch_id,
                file_id=file_id,
                potential_chunk_numbers=[3],
                excluded_child_chunk_ids=["child-1", "child-2"],
            ), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0
        return _filter_batch(
            batch_id,
            file_id=file_id,
            potential_chunk_numbers=[],
        ), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0

    async def _fake_verifier_batch(_state, *, file_filtering_result, batch_id):
        return _verifier_batch(batch_id), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0

    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_search_and_group_batch", _fake_search_batch)
    monkeypatch.setattr(
        iterative_search_filter_orchestrator_node,
        "run_non_strong_signal_file_context_expansion_batch",
        _fake_expand_batch,
    )
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_file_filtering_batch", _fake_filter_batch)
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_parent_chunk_constraint_verifier_batch", _fake_verifier_batch)
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(iterative_search_filter_orchestrator_node.iterative_search_filter_orchestrator_node(state))
    assert result["node2_search_group_result"]["run_summary"]["batch_count"] == 2
    assert result["node4_file_filtering_result"]["run_summary"]["termination_reason"] == "search_exhausted"
    assert len(seen_search_kwargs) >= 2


def test_orchestrator_prefetch_starts_before_parent_chunk_constraint_verifier(monkeypatch):
    state = _base_state()
    prefetch_started = asyncio.Event()
    allow_prefetch_finish = asyncio.Event()
    verifier_saw_prefetch = {"value": False}

    async def _fake_search_batch(_state, **kwargs):
        batch_id = int(kwargs.get("batch_id", 1))
        if batch_id == 1:
            return _search_batch(1, "file-a", strong=False)
        prefetch_started.set()
        await allow_prefetch_finish.wait()
        return _search_batch(batch_id, None, strong=False)

    async def _fake_expand_batch(_state, *, search_group_result, batch_id):
        files = search_group_result.get("files", [])
        file_id = files[0]["file_id"] if files else None
        return _expansion_batch(batch_id, file_id)

    async def _fake_filter_batch(_state, *, search_group_result, expansion_result, batch_id):
        files = search_group_result.get("files", [])
        file_id = files[0]["file_id"] if files else None
        return _filter_batch(
            batch_id,
            file_id=file_id,
            potential_chunk_numbers=[1],
        ), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0

    async def _fake_verifier_batch(_state, *, file_filtering_result, batch_id):
        verifier_saw_prefetch["value"] = prefetch_started.is_set()
        allow_prefetch_finish.set()
        return _verifier_batch(batch_id), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0

    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_search_and_group_batch", _fake_search_batch)
    monkeypatch.setattr(
        iterative_search_filter_orchestrator_node,
        "run_non_strong_signal_file_context_expansion_batch",
        _fake_expand_batch,
    )
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_file_filtering_batch", _fake_filter_batch)
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_parent_chunk_constraint_verifier_batch", _fake_verifier_batch)
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "log_modification_agent_search_group", lambda **kwargs: None)

    asyncio.run(iterative_search_filter_orchestrator_node.iterative_search_filter_orchestrator_node(state))
    assert verifier_saw_prefetch["value"] is True


def test_orchestrator_stops_when_no_potential_parent_chunks(monkeypatch):
    state = _base_state()

    async def _fake_search_batch(_state, **kwargs):
        return _search_batch(1, "file-a", strong=True)

    async def _fake_expand_batch(_state, *, search_group_result, batch_id):
        return _expansion_batch(batch_id, "file-a")

    async def _fake_filter_batch(_state, *, search_group_result, expansion_result, batch_id):
        return _filter_batch(
            batch_id,
            file_id="file-a",
            potential_chunk_numbers=[],
        ), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0

    async def _fake_verifier_batch(_state, *, file_filtering_result, batch_id):
        return _verifier_batch(batch_id), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0

    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_search_and_group_batch", _fake_search_batch)
    monkeypatch.setattr(
        iterative_search_filter_orchestrator_node,
        "run_non_strong_signal_file_context_expansion_batch",
        _fake_expand_batch,
    )
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_file_filtering_batch", _fake_filter_batch)
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_parent_chunk_constraint_verifier_batch", _fake_verifier_batch)
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(iterative_search_filter_orchestrator_node.iterative_search_filter_orchestrator_node(state))
    assert result["node2_search_group_result"]["run_summary"]["batch_count"] == 1
    assert result["node4_file_filtering_result"]["run_summary"]["termination_reason"] == "no_potential_parent_chunks"


def test_orchestrator_merges_confirmed_refs_from_node4_and_node5(monkeypatch):
    state = _base_state()

    async def _fake_search_batch(_state, **kwargs):
        batch_id = int(kwargs.get("batch_id", 1))
        if batch_id == 1:
            return _search_batch(1, "file-a", strong=True)
        return _search_batch(batch_id, None, strong=False)

    async def _fake_expand_batch(_state, *, search_group_result, batch_id):
        return _expansion_batch(batch_id, "file-a")

    async def _fake_filter_batch(_state, *, search_group_result, expansion_result, batch_id):
        return _filter_batch(
            batch_id,
            file_id="file-a",
            potential_chunk_numbers=[3],
            confirmed_chunk_numbers=[1],
        ), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0

    async def _fake_verifier_batch(_state, *, file_filtering_result, batch_id):
        return _verifier_batch(
            batch_id,
            refs=[{"file_id": "file-a", "file_name": "file-a.md", "parent_chunk_number": 2}],
        ), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0

    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_search_and_group_batch", _fake_search_batch)
    monkeypatch.setattr(
        iterative_search_filter_orchestrator_node,
        "run_non_strong_signal_file_context_expansion_batch",
        _fake_expand_batch,
    )
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_file_filtering_batch", _fake_filter_batch)
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_parent_chunk_constraint_verifier_batch", _fake_verifier_batch)
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(iterative_search_filter_orchestrator_node.iterative_search_filter_orchestrator_node(state))
    merged_refs = result["node5_parent_chunk_constraint_verifier_result"]["merged_confirmed_parent_chunk_refs"]
    assert sorted(item["parent_chunk_number"] for item in merged_refs) == [1, 2]
