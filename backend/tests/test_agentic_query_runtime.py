import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.agentic_query import runtime
from app.service.rag.agentic_query.models import EvidenceItem


def _llm_sequence(responses: list[str]):
    state = {"index": 0}

    async def _fake_call_action_model(**_kwargs):
        index = state["index"]
        state["index"] += 1
        if index >= len(responses):
            return responses[-1], {}
        return responses[index], {}

    return _fake_call_action_model


def test_runtime_rejects_unknown_action_and_recovers_with_finish(monkeypatch):
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"unknown_tool","arguments":{}}',
                '{"action":"finish","arguments":{"answer":"Recovered answer","citations":[]}}',
            ]
        ),
    )

    async def _fake_search_context_tool(**_kwargs):
        return []

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="Where is the refund period?",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=2,
        )
    )

    assert result.answer == "Recovered answer"
    assert result.termination_reason == "finished"
    # Seed retrieval is counted; unknown action should not trigger extra tool execution.
    assert result.tool_call_count == 1


def test_runtime_enforces_max_steps(monkeypatch):
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"search_context","arguments":{"query":"q","top_k":2}}',
                '{"action":"search_context","arguments":{"query":"q","top_k":2}}',
                '{"action":"search_context","arguments":{"query":"q","top_k":2}}',
            ]
        ),
    )

    async def _fake_search_context_tool(**_kwargs):
        return []

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="Tell me about refund policy",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=2,
        )
    )

    assert result.termination_reason == "max_steps_exceeded"
    assert result.answer == "No answer found in the provided context."


def test_runtime_reads_reference_only_on_demand(monkeypatch):
    calls = {"count": 0}

    def _fake_read_reference_content(_config, _ref_id, *, max_chars: int = 3000):
        _ = max_chars
        calls["count"] += 1
        return "Reference content"

    monkeypatch.setattr(runtime.tools, "read_reference_content", _fake_read_reference_content)

    async def _fake_search_context_tool(**_kwargs):
        return []

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            ['{"action":"finish","arguments":{"answer":"Done","citations":[]}}']
        ),
    )
    _ = asyncio.run(
        runtime.run_agentic_query(
            user_query="Q1",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=1,
        )
    )
    assert calls["count"] == 0

    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"read_reference","arguments":{"ref_id":"citation_policy"}}',
                '{"action":"finish","arguments":{"answer":"Done","citations":[]}}',
            ]
        ),
    )
    _ = asyncio.run(
        runtime.run_agentic_query(
            user_query="Q2",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=2,
        )
    )
    assert calls["count"] == 1


def test_runtime_normalizes_citations_to_scoped_evidence(monkeypatch):
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"search_context","arguments":{"query":"refund","top_k":2}}',
                '{"action":"finish","arguments":{"answer":"Final","citations":["policy.md","BAD.md","POLICY.MD"]}}',
            ]
        ),
    )

    async def _fake_search_context_tool(**_kwargs):
        return [
            EvidenceItem(
                parent_id="parent-1",
                file_id="file-a",
                file_name="policy.md",
                parent_chunk_number=1,
                snippet="Policy snippet",
            )
        ]

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="What is refund policy?",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=2,
        )
    )

    assert result.answer == "Final"
    assert result.citations == ["policy.md"]


def test_runtime_forced_finish_uses_cached_evidence_after_repeated_search(monkeypatch):
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"search_context","arguments":{"query":"speed of light","top_k":5}}',
                '{"action":"search_context","arguments":{"query":"speed of light","top_k":5}}',
                '{"action":"finish","arguments":{"answer":"The speed of light in a vacuum is exactly 0 meters per second.","citations":["Random Facts.pdf"]}}',
            ]
        ),
    )

    async def _fake_search_context_tool(**kwargs):
        parent_doc_cache = kwargs["parent_doc_cache"]
        parent_doc_cache["parent-speed"] = {
            "id": "parent-speed",
            "page_content": "Science facts. 4. The speed of light in a vacuum is exactly 0 meters per second.",
            "_agentic_query_snippet": "4. The speed of light in a vacuum is exactly 0 meters per second.",
            "metadata": {
                "user_id": "user-1",
                "file_metadata": {
                    "file_id": "file-speed",
                    "file_name": "Random Facts.pdf",
                },
                "parent_chunk_metadata": {
                    "parent_chunk_number": 1,
                },
            },
        }
        return [
            EvidenceItem(
                parent_id="parent-speed",
                file_id="file-speed",
                file_name="Random Facts.pdf",
                parent_chunk_number=1,
                snippet="4. The speed of light in a vacuum is exactly 0 meters per second.",
            )
        ]

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="What is the speed of light",
            user_id="user-1",
            included_file_ids=["file-speed"],
            max_steps=2,
        )
    )

    assert result.termination_reason == "forced_finish_after_max_steps"
    assert result.answer == "The speed of light in a vacuum is exactly 0 meters per second."
    assert result.citations == ["Random Facts.pdf"]


def test_runtime_emits_structured_step_trace_metadata(monkeypatch):
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                (
                    '{"action":"search_context","arguments":{"query":"refund","top_k":2},'
                    '"intent":"Find policy evidence","decision":"If weak, fetch parent chunk"}'
                ),
                (
                    '{"action":"finish","arguments":{"answer":"Refund period is 30 days.","citations":["policy.md"]},'
                    '"intent":"Enough evidence to answer","decision":"Stop"}'
                ),
            ]
        ),
    )

    async def _fake_search_context_tool(**_kwargs):
        return [
            EvidenceItem(
                parent_id="parent-1",
                file_id="file-a",
                file_name="policy.md",
                parent_chunk_number=1,
                snippet="Refund period is 30 days.",
            )
        ]

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    progress_events: list[dict] = []

    async def _collect_progress(event):
        progress_events.append(dict(event))

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="What is the refund period?",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=2,
            progress_callback=_collect_progress,
        )
    )

    assert result.termination_reason == "finished"
    step_started = [
        event
        for event in progress_events
        if event.get("stage") == "agentic_query_step"
        and event.get("status") == "started"
        and isinstance(event.get("metadata"), dict)
        and event["metadata"].get("action") == "search_context"
    ]
    assert step_started, "Expected started step event for search_context."
    assert step_started[0]["metadata"].get("intent") == "Find policy evidence"
    assert step_started[0]["metadata"].get("decision") == "If weak, fetch parent chunk"
    assert step_started[0]["metadata"].get("tool") == "search_context"

    step_completed = [
        event
        for event in progress_events
        if event.get("stage") == "agentic_query_step"
        and event.get("status") == "completed"
        and isinstance(event.get("metadata"), dict)
        and event["metadata"].get("action") == "search_context"
    ]
    assert step_completed, "Expected completed step event for search_context."
    assert isinstance(step_completed[0]["metadata"].get("argumentsPreview"), str)
    assert "search_context returned" in str(step_completed[0]["metadata"].get("observation"))
