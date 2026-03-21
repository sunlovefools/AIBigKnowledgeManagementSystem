import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.agentic_modification.nodes import iterative_search_filter_orchestrator_node
from app.service.rag.agentic_modification.shared.constants import (
    ITERATION_CONTINUE_PROMOTED_RATIO_THRESHOLD,
)


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
    file_id: str | None,
    promoted_count: int,
    *,
    evaluated_count: int = 1,
) -> dict:
    promoted_files = []
    if promoted_count > 0:
        base_file_id = file_id or f"batch-{batch_id}-file"
        for index in range(promoted_count):
            promoted_file_id = base_file_id if index == 0 else f"{base_file_id}-p{index}"
            promoted_files.append(
                {
                    "file_id": promoted_file_id,
                    "file_name": f"{promoted_file_id}.md",
                    "promotion_reason": "direct_match",
                    "confidence": 1.0,
                    "decision": "direct_match",
                }
            )
    promoted_ratio = (
        float(promoted_count) / float(evaluated_count)
        if evaluated_count > 0
        else 0.0
    )
    return {
        "batch_id": batch_id,
        "goal": "Update policy.",
        "constraint": "Only policy section.",
        "confidence_threshold_for_potential_match": 0.70,
        "strong_signal_files": [],
        "evaluations": [],
        "promoted_files": promoted_files,
        "dropped_files": [],
        "run_summary": {
            "promoted_file_count": promoted_count,
            "evaluated_file_count": evaluated_count,
            "evaluated_non_strong_file_count": evaluated_count,
            "dropped_file_count": max(0, evaluated_count - promoted_count),
            "promoted_ratio_current_batch": promoted_ratio,
            "continue_ratio_threshold": ITERATION_CONTINUE_PROMOTED_RATIO_THRESHOLD,
        },
    }


def test_orchestrator_excludes_all_fetched_file_ids_between_batches(monkeypatch):
    state = _base_state()
    seen_excluded: list[set[str]] = []

    async def _fake_search_batch(_state, *, excluded_file_ids, batch_id):
        seen_excluded.append(set(excluded_file_ids))
        if batch_id == 1:
            return _search_batch(1, "file-a", strong=True)
        if batch_id == 2:
            return _search_batch(2, "file-b", strong=False)
        return _search_batch(batch_id, None, strong=False)

    async def _fake_expand_batch(_state, *, search_group_result, batch_id):
        files = search_group_result.get("files", [])
        file_id = files[0]["file_id"] if files else None
        return _expansion_batch(batch_id, file_id)

    async def _fake_filter_batch(_state, *, search_group_result, expansion_result, batch_id):
        files = search_group_result.get("files", [])
        file_id = files[0]["file_id"] if files else None
        promoted_count = 1 if batch_id == 1 else 6
        evaluated_count = 1 if batch_id == 1 else 10
        return _filter_batch(
            batch_id,
            file_id,
            promoted_count=promoted_count,
            evaluated_count=evaluated_count,
        ), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0

    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_search_and_group_batch", _fake_search_batch)
    monkeypatch.setattr(
        iterative_search_filter_orchestrator_node,
        "run_non_strong_signal_file_context_expansion_batch",
        _fake_expand_batch,
    )
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_file_filtering_batch", _fake_filter_batch)
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(iterative_search_filter_orchestrator_node.iterative_search_filter_orchestrator_node(state))
    node2 = result["node2_search_group_result"]

    assert seen_excluded[0] == set()
    assert seen_excluded[1] == {"file-a"}
    assert node2["run_summary"]["batch_count"] == 2
    assert node2["run_summary"]["termination_reason"] == "promotion_ratio_below_threshold"


def test_orchestrator_prefetch_starts_while_filtering_runs(monkeypatch):
    state = _base_state()
    prefetch_started = asyncio.Event()
    allow_prefetch_finish = asyncio.Event()
    marker = {"started_before_filter": False}

    async def _fake_search_batch(_state, *, excluded_file_ids, batch_id):
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
        marker["started_before_filter"] = prefetch_started.is_set()
        allow_prefetch_finish.set()
        files = search_group_result.get("files", [])
        file_id = files[0]["file_id"] if files else None
        return _filter_batch(
            batch_id,
            file_id,
            promoted_count=1,
            evaluated_count=1,
        ), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0

    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_search_and_group_batch", _fake_search_batch)
    monkeypatch.setattr(
        iterative_search_filter_orchestrator_node,
        "run_non_strong_signal_file_context_expansion_batch",
        _fake_expand_batch,
    )
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_file_filtering_batch", _fake_filter_batch)
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "log_modification_agent_search_group", lambda **kwargs: None)

    asyncio.run(iterative_search_filter_orchestrator_node.iterative_search_filter_orchestrator_node(state))
    assert marker["started_before_filter"] is True


def test_orchestrator_stops_immediately_and_cancels_prefetch_when_ratio_below_threshold(monkeypatch):
    state = _base_state()
    cancelled = {"value": False}

    async def _fake_search_batch(_state, *, excluded_file_ids, batch_id):
        if batch_id == 1:
            return _search_batch(1, "file-a", strong=True)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled["value"] = True
            raise
        return _search_batch(batch_id, "file-b", strong=False)

    async def _fake_expand_batch(_state, *, search_group_result, batch_id):
        files = search_group_result.get("files", [])
        file_id = files[0]["file_id"] if files else None
        return _expansion_batch(batch_id, file_id)

    async def _fake_filter_batch(_state, *, search_group_result, expansion_result, batch_id):
        return _filter_batch(
            batch_id,
            None,
            promoted_count=0,
            evaluated_count=10,
        ), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0

    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_search_and_group_batch", _fake_search_batch)
    monkeypatch.setattr(
        iterative_search_filter_orchestrator_node,
        "run_non_strong_signal_file_context_expansion_batch",
        _fake_expand_batch,
    )
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_file_filtering_batch", _fake_filter_batch)
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(iterative_search_filter_orchestrator_node.iterative_search_filter_orchestrator_node(state))
    assert result["node4_file_filtering_result"]["run_summary"]["termination_reason"] == "promotion_ratio_below_threshold"
    assert cancelled["value"] is True


def test_orchestrator_deduplicates_global_promoted_files(monkeypatch):
    state = _base_state()

    async def _fake_search_batch(_state, *, excluded_file_ids, batch_id):
        if batch_id == 1:
            return _search_batch(1, "file-a", strong=True)
        if batch_id == 2:
            return _search_batch(2, "file-a", strong=True)
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
                file_id,
                promoted_count=1,
                evaluated_count=1,
            ), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0
        return _filter_batch(
            batch_id,
            None,
            promoted_count=0,
            evaluated_count=10,
        ), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0

    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_search_and_group_batch", _fake_search_batch)
    monkeypatch.setattr(
        iterative_search_filter_orchestrator_node,
        "run_non_strong_signal_file_context_expansion_batch",
        _fake_expand_batch,
    )
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_file_filtering_batch", _fake_filter_batch)
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(iterative_search_filter_orchestrator_node.iterative_search_filter_orchestrator_node(state))
    promoted = result["node4_file_filtering_result"]["promoted_files"]
    assert len(promoted) == 1
    assert promoted[0]["file_id"] == "file-a"


def test_orchestrator_continues_when_promoted_ratio_meets_threshold(monkeypatch):
    state = _base_state()
    seen_batches: list[int] = []

    async def _fake_search_batch(_state, *, excluded_file_ids, batch_id):
        seen_batches.append(batch_id)
        if batch_id == 1:
            return _search_batch(1, "file-a", strong=False)
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
            file_id,
            promoted_count=7,
            evaluated_count=10,
        ), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0

    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_search_and_group_batch", _fake_search_batch)
    monkeypatch.setattr(
        iterative_search_filter_orchestrator_node,
        "run_non_strong_signal_file_context_expansion_batch",
        _fake_expand_batch,
    )
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_file_filtering_batch", _fake_filter_batch)
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(iterative_search_filter_orchestrator_node.iterative_search_filter_orchestrator_node(state))
    assert result["node2_search_group_result"]["run_summary"]["batch_count"] == 2
    assert result["node2_search_group_result"]["run_summary"]["termination_reason"] == "search_exhausted"
    assert seen_batches[:2] == [1, 2]


def test_orchestrator_stops_when_promoted_ratio_below_threshold(monkeypatch):
    state = _base_state()
    cancelled = {"value": False}
    started = {"value": False}

    async def _fake_search_batch(_state, *, excluded_file_ids, batch_id):
        if batch_id == 1:
            return _search_batch(1, "file-a", strong=False)
        started["value"] = True
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled["value"] = True
            raise
        return _search_batch(batch_id, "file-b", strong=False)

    async def _fake_expand_batch(_state, *, search_group_result, batch_id):
        files = search_group_result.get("files", [])
        file_id = files[0]["file_id"] if files else None
        return _expansion_batch(batch_id, file_id)

    async def _fake_filter_batch(_state, *, search_group_result, expansion_result, batch_id):
        files = search_group_result.get("files", [])
        file_id = files[0]["file_id"] if files else None
        return _filter_batch(
            batch_id,
            file_id,
            promoted_count=6,
            evaluated_count=10,
        ), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0

    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_search_and_group_batch", _fake_search_batch)
    monkeypatch.setattr(
        iterative_search_filter_orchestrator_node,
        "run_non_strong_signal_file_context_expansion_batch",
        _fake_expand_batch,
    )
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "run_file_filtering_batch", _fake_filter_batch)
    monkeypatch.setattr(iterative_search_filter_orchestrator_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(iterative_search_filter_orchestrator_node.iterative_search_filter_orchestrator_node(state))
    assert result["node2_search_group_result"]["run_summary"]["batch_count"] == 1
    assert result["node2_search_group_result"]["run_summary"]["termination_reason"] == "promotion_ratio_below_threshold"
    assert started["value"] is True
    assert cancelled["value"] is True
