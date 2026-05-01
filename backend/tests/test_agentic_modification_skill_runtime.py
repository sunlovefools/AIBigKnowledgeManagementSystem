import asyncio
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.agentic_modification_skill import llm_client, runtime, tools
from app.service.rag.agentic_modification_skill.config_loader import (
    load_agentic_modification_skill_config,
)
from app.service.rag.agentic_modification_skill.models import EvidenceItem


class _FakeParentCollection:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def find(self, filter=None, projection=None):
        _ = projection
        filter = filter or {}
        return [row for row in self.rows if self._matches(row, filter)]

    def _matches(self, row: dict, filter_doc: dict) -> bool:
        for key, expected in filter_doc.items():
            actual = self._get_path(row, key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in set(expected["$in"]):
                    return False
                continue
            if actual != expected:
                return False
        return True

    def _get_path(self, row: dict, dotted_path: str):
        current = row
        for part in dotted_path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current


class _FakeParentStore:
    def __init__(self, rows: list[dict]):
        self.collection = _FakeParentCollection(rows)
        self.by_id = {str(row["_id"]): row["value"] for row in rows}

    async def amget(self, ids):
        return [self.by_id.get(str(item)) for item in ids]


def _parent_row(
    *,
    parent_id: str,
    file_id: str,
    file_name: str,
    chunk_number: int,
    content: str,
    user_id: str = "user-1",
) -> dict:
    return {
        "_id": parent_id,
        "value": {
            "page_content": content,
            "metadata": {
                "user_id": user_id,
                "file_metadata": {"file_id": file_id, "file_name": file_name},
                "parent_chunk_metadata": {"parent_chunk_number": chunk_number},
            },
            "type": "Document",
        },
    }


def _install_fake_vectordb(monkeypatch, rows: list[dict]):
    fake_vectordb = types.ModuleType("app.vectordb.vectordb")
    fake_vectordb.PARENT_STORE = _FakeParentStore(rows)

    async def _fake_search_and_retrieve_context(**kwargs):
        query = str(kwargs.get("query") or "").casefold()
        user_id = kwargs.get("user_id")
        included = kwargs.get("included_file_ids")
        included_set = None if included is None else set(included)
        docs = []
        for row in rows:
            doc = dict(row["value"])
            doc["id"] = row["_id"]
            metadata = doc["metadata"]
            file_id = metadata["file_metadata"]["file_id"]
            if metadata["user_id"] != user_id:
                continue
            if included_set is not None and file_id not in included_set:
                continue
            if query in doc["page_content"].casefold():
                docs.append(doc)
        return docs

    fake_vectordb.search_and_retrieve_context = _fake_search_and_retrieve_context
    monkeypatch.setitem(sys.modules, "app.vectordb.vectordb", fake_vectordb)


def _llm_sequence(responses: list[str]):
    state = {"index": 0}

    async def _fake_call_action_model(**_kwargs):
        index = state["index"]
        state["index"] += 1
        if index >= len(responses):
            return responses[-1], {}
        return responses[index], {}

    return _fake_call_action_model


def test_config_loader_indexes_document_modification_skill():
    load_agentic_modification_skill_config.cache_clear()
    config = load_agentic_modification_skill_config()

    assert "document-modification" in config.skill_registry
    metadata = config.skill_registry["document-modification"]
    assert "delegate_file_edits" in metadata.allowed_tools
    assert "worker_protocol" in metadata.reference_ids


def test_deepseek_runtime_config_disables_thinking_by_default(monkeypatch):
    monkeypatch.delenv("AGENTIC_MODIFICATION_SKILL_THINKING", raising=False)
    monkeypatch.delenv("MOD_AGENT_LLM_THINKING", raising=False)
    monkeypatch.setenv("AGENTIC_MODIFICATION_SKILL_LLM_URL", "https://api.deepseek.com/v1/chat/completions")
    monkeypatch.setenv("AGENTIC_MODIFICATION_SKILL_LLM_KEY", "test-key")
    monkeypatch.setenv("AGENTIC_MODIFICATION_SKILL_LLM_MODEL", "deepseek-v4-flash")

    _url, _api_key, _model, thinking = llm_client._resolve_runtime_config()

    assert thinking == "disabled"


def test_reasoning_content_is_not_used_as_action_payload():
    content = llm_client._extract_message_text(
        {
            "message": {
                "reasoning_content": '{"action":"finish","arguments":{"summary":"from reasoning"}}',
                "content": '{"action":"finish","arguments":{"summary":"from content"}}',
            }
        }
    )

    assert content == '{"action":"finish","arguments":{"summary":"from content"}}'


def test_modification_skill_prefers_canonical_llm_envs(monkeypatch):
    monkeypatch.setenv("LLM_API_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_API_KEY", "canonical-key")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LLM_THINKING", "disabled")
    monkeypatch.setenv("AGENTIC_MODIFICATION_SKILL_LLM_URL", "https://legacy-mod.example/v1/chat/completions")
    monkeypatch.setenv("AGENTIC_MODIFICATION_SKILL_LLM_KEY", "legacy-mod-key")
    monkeypatch.setenv("AGENTIC_MODIFICATION_SKILL_LLM_MODEL", "legacy-mod-model")

    url, api_key, model, thinking = llm_client._resolve_runtime_config()

    assert url == "https://api.deepseek.com/chat/completions"
    assert api_key == "canonical-key"
    assert model == "deepseek-v4-flash"
    assert thinking == "disabled"


def test_fetch_file_outline_and_window_are_scoped_and_ordered(monkeypatch):
    rows = [
        _parent_row(parent_id="p2", file_id="file-a", file_name="policy.md", chunk_number=1, content="# Later\nRefund is 14 days."),
        _parent_row(parent_id="p1", file_id="file-a", file_name="policy.md", chunk_number=0, content="# Start\nIntro."),
        _parent_row(parent_id="p3", file_id="file-b", file_name="other.md", chunk_number=1, content="Refund is 7 days."),
    ]
    _install_fake_vectordb(monkeypatch, rows)
    cache: dict = {}

    outline = asyncio.run(
        tools.fetch_file_outline_tool(
            file_id="file-a",
            file_name=None,
            max_chunks=10,
            user_id="user-1",
            included_file_ids=["file-a"],
            parent_doc_cache=cache,
        )
    )

    assert [item.parent_id for item in outline] == ["p1", "p2"]
    assert [item.parent_chunk_number for item in outline] == [0, 1]
    assert outline[0].preview == "# Start Intro."

    window = asyncio.run(
        tools.fetch_chunk_window_tool(
            file_id="file-a",
            center_parent_id="p2",
            center_chunk_number=None,
            before=1,
            after=1,
            user_id="user-1",
            included_file_ids=["file-a"],
            parent_doc_cache=cache,
        )
    )

    assert [item.parent_id for item in window.chunks] == ["p1", "p2"]
    assert window.chunks[1].content == "# Later\nRefund is 14 days."


def test_search_files_matches_meeting_minutes_from_full_edit_prompt(monkeypatch):
    rows = [
        _parent_row(
            parent_id="m1",
            file_id="minutes-1",
            file_name="Meeting Minutes - January.md",
            chunk_number=0,
            content="Attendees: Alice, Bob",
        ),
        _parent_row(
            parent_id="m2",
            file_id="minutes-2",
            file_name="Meeting Minutes - February.md",
            chunk_number=0,
            content="Attendees: Carol, Dan",
        ),
        _parent_row(
            parent_id="p1",
            file_id="policy-1",
            file_name="Policy.md",
            chunk_number=0,
            content="Attendees are not relevant here.",
        ),
    ]
    _install_fake_vectordb(monkeypatch, rows)

    matches = asyncio.run(
        tools.search_files_tool(
            query='Add a "Penny" to attendees for all the meeting minutes',
            limit=10,
            user_id="user-1",
            included_file_ids=["minutes-1", "minutes-2", "policy-1"],
        )
    )

    assert {item.file_id for item in matches[:2]} == {"minutes-1", "minutes-2"}
    assert "policy-1" not in {item.file_id for item in matches}


def test_runtime_delegates_multiple_files_and_returns_parent_proposals(monkeypatch):
    rows = [
        _parent_row(parent_id="p-a", file_id="file-a", file_name="a.md", chunk_number=1, content="Refund is 14 days."),
        _parent_row(parent_id="p-b", file_id="file-b", file_name="b.md", chunk_number=1, content="Refund is 14 days."),
    ]
    _install_fake_vectordb(monkeypatch, rows)
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"delegate_file_edits","arguments":{"file_ids":["file-a","file-b"],"instruction":"Change refund to 30 days."}}',
                '{"targets":[{"parent_id":"p-a","reason":"refund period"}]}',
                '{"targets":[{"parent_id":"p-b","reason":"refund period"}]}',
                '{"proposals":[{"parent_id":"p-a","proposed":"Refund is 30 days."}]}',
                '{"proposals":[{"parent_id":"p-b","proposed":"Refund is 30 days."}]}',
                '{"action":"finish","arguments":{"summary":"done"}}',
            ]
        ),
    )
    monkeypatch.setattr(tools.llm_client, "call_action_model", runtime.llm_client.call_action_model)

    result = asyncio.run(
        runtime.run_agentic_modification_skill(
            user_instruction="Change refund to 30 days.",
            user_id="user-1",
            included_file_ids=["file-a", "file-b"],
            max_steps=3,
        )
    )

    assert result.termination_reason == "finished"
    assert sorted(proposal.fileId for proposal in result.proposals) == ["file-a", "file-b"]
    assert all(proposal.original == "Refund is 14 days." for proposal in result.proposals)
    assert all(proposal.proposed == "Refund is 30 days." for proposal in result.proposals)


def test_runtime_seeded_meeting_minutes_candidates_prevent_empty_finish(monkeypatch):
    rows = [
        _parent_row(
            parent_id="m1",
            file_id="minutes-1",
            file_name="Meeting Minutes - January.md",
            chunk_number=0,
            content="Attendees: Alice, Bob",
        ),
        _parent_row(
            parent_id="m2",
            file_id="minutes-2",
            file_name="Meeting Minutes - February.md",
            chunk_number=0,
            content="Attendees: Carol, Dan",
        ),
    ]
    _install_fake_vectordb(monkeypatch, rows)
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"finish","arguments":{"summary":"no changes"}}',
                '{"action":"delegate_file_edits","arguments":{"file_ids":["minutes-1","minutes-2"],"instruction":"Add Penny to attendees."}}',
                '{"targets":[{"parent_id":"m1","reason":"attendees list"}]}',
                '{"targets":[{"parent_id":"m2","reason":"attendees list"}]}',
                '{"proposals":[{"parent_id":"m1","proposed":"Attendees: Alice, Bob, Penny"}]}',
                '{"proposals":[{"parent_id":"m2","proposed":"Attendees: Carol, Dan, Penny"}]}',
                '{"action":"finish","arguments":{"summary":"done"}}',
            ]
        ),
    )
    monkeypatch.setattr(tools.llm_client, "call_action_model", runtime.llm_client.call_action_model)

    result = asyncio.run(
        runtime.run_agentic_modification_skill(
            user_instruction='Add a "Penny" to attendees for all the meeting minutes',
            user_id="user-1",
            included_file_ids=["minutes-1", "minutes-2"],
            max_steps=4,
        )
    )

    assert result.termination_reason == "finished"
    assert sorted(proposal.fileId for proposal in result.proposals) == ["minutes-1", "minutes-2"]
    assert result.coverage_report["discovered_candidate_files"] == [
        {"file_id": "minutes-1", "file_name": "Meeting Minutes - January.md"},
        {"file_id": "minutes-2", "file_name": "Meeting Minutes - February.md"},
    ]


def test_runtime_forces_delegation_after_exploration_only_steps(monkeypatch):
    rows = [
        _parent_row(
            parent_id="m1",
            file_id="minutes-1",
            file_name="Meeting Minutes - January.md",
            chunk_number=0,
            content="Attendees: Alice, Bob",
        ),
        _parent_row(
            parent_id="m2",
            file_id="minutes-2",
            file_name="Meeting Minutes - February.md",
            chunk_number=0,
            content="Attendees: Carol, Dan",
        ),
    ]
    _install_fake_vectordb(monkeypatch, rows)
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"load_skill","arguments":{"skill_name":"document-modification"}}',
                '{"action":"fetch_file_outline","arguments":{"file_id":"minutes-1"}}',
                '{"action":"fetch_parent_chunk","arguments":{"parent_id":"m1"}}',
                '{"targets":[{"parent_id":"m1","reason":"attendees list"}]}',
                '{"targets":[{"parent_id":"m2","reason":"attendees list"}]}',
                '{"proposals":[{"parent_id":"m1","proposed":"Attendees: Alice, Bob, Penny"}]}',
                '{"proposals":[{"parent_id":"m2","proposed":"Attendees: Carol, Dan, Penny"}]}',
            ]
        ),
    )
    monkeypatch.setattr(tools.llm_client, "call_action_model", runtime.llm_client.call_action_model)

    result = asyncio.run(
        runtime.run_agentic_modification_skill(
            user_instruction='Add a "Penny" to attendees for all the meeting minutes',
            user_id="user-1",
            included_file_ids=["minutes-1", "minutes-2"],
            max_steps=3,
        )
    )

    assert result.termination_reason == "forced_delegate_after_max_steps"
    assert sorted(proposal.fileId for proposal in result.proposals) == ["minutes-1", "minutes-2"]
    assert sorted(result.coverage_report["delegated_files"]) == ["minutes-1", "minutes-2"]


def test_runtime_rejects_finish_with_uncovered_candidate(monkeypatch):
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"search_context","arguments":{"query":"refund","top_k":2}}',
                '{"action":"finish","arguments":{"summary":"too early"}}',
                '{"action":"finish","arguments":{"skipped_candidates":[{"file_id":"file-a","reason":"No edit needed."}]}}',
            ]
        ),
    )

    async def _fake_search_context_tool(**_kwargs):
        return [
            EvidenceItem(
                parent_id="p-a",
                file_id="file-a",
                file_name="a.md",
                parent_chunk_number=1,
                snippet="Refund is 14 days.",
            )
        ]

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    result = asyncio.run(
        runtime.run_agentic_modification_skill(
            user_instruction="Change refund to 30 days.",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=3,
        )
    )

    assert result.termination_reason == "finished"
    assert result.proposals == []
    assert result.coverage_report["skipped_candidates"] == [
        {"file_id": "file-a", "reason": "No edit needed."}
    ]
