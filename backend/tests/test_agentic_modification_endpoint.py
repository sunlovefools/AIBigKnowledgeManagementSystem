import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api import router_agent


def _build_payload(**overrides):
    payload = {
        "user_instructions": "Change the refund day from 14 days to 30 days for UK refund policy.",
        "fileIds": None,
        "collectionId": None,
    }
    payload.update(overrides)
    return router_agent.AgenticModificationRequest(**payload)


def _patch_collection_scope(monkeypatch, *, file_ids: list[str] | None = None):
    async def _resolve_active_collection(*, user_id: str, requested_collection_id: str | None = None):
        _ = user_id, requested_collection_id
        return {"collection_id": "collection-default", "name": "Default"}

    async def _list_file_ids_for_collection(*, user_id: str, collection_id: str):
        _ = user_id, collection_id
        if file_ids is None:
            return ["file-1", "file-2", "file-a", "file-b"]
        return file_ids

    monkeypatch.setattr(router_agent.CollectionService, "resolve_active_collection", _resolve_active_collection)
    monkeypatch.setattr(router_agent.CollectionService, "list_file_ids_for_collection", _list_file_ids_for_collection)


class _FakeGraph:
    async def ainvoke(self, state):
        return {
            **state,
            "goal": "Update UK refund policy from 14 days to 30 days.",
            "lexical_anchors": ["14 days", "UK", "refund policy"],
            "semantic_anchors": ["UK refund policy", "refund policy with 14 days period"],
            "anchors": [
                "14 days",
                "UK",
                "refund policy",
                "UK refund policy",
                "refund policy with 14 days period",
            ],
            "constraint": "Only update text that applies to UK refund policy.",
            "intention": "edit",
            "proposals": [
                {
                    "fileId": "file-1",
                    "fileName": "policy.md",
                    "parentId": "parent-1",
                    "original": "Refund period is 14 days.",
                    "proposed": "Refund period is 30 days.",
                    "source": "agent",
                }
            ],
            "node2_search_group_result": {
                "run_summary": {
                    "top_k_per_query": 15,
                }
            },
            "node3_non_strong_signal_file_context_expansion_result": {
                "run_summary": {
                    "expanded_file_count": 1,
                }
            },
            "node4_file_filtering_result": {
                "run_summary": {
                    "promoted_file_count": 1,
                }
            },
            "node5_parent_chunk_constraint_verifier_result": {
                "run_summary": {
                    "confirmed_parent_chunk_ref_count": 1,
                }
            },
            "node6_editor_result": {
                "run_summary": {
                    "edited_parent_chunk_count": 1,
                }
            },
            "token_prompt_total": 10,
            "token_completion_total": 8,
            "token_total": 18,
            "llm_call_count": 1,
            "error": None,
        }


def test_agentic_modify_rejects_empty_user_instructions():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            router_agent.agentic_modify(
                _build_payload(user_instructions="   "),
                current_user={"sub": "user-1"},
            )
        )

    assert exc.value.status_code == 422


def test_agentic_modify_returns_retrieval_brief_and_node_outputs(monkeypatch):
    monkeypatch.setattr(router_agent, "log_token_usage", lambda **kwargs: None)
    monkeypatch.setattr(router_agent, "_load_retrieval_graph", lambda: _FakeGraph())
    _patch_collection_scope(monkeypatch)

    response = asyncio.run(router_agent.agentic_modify(_build_payload(), current_user={"sub": "user-1"}))
    payload = response.model_dump()

    assert payload == {
        "intention": "edit",
        "proposals": [
            {
                "fileId": "file-1",
                "fileName": "policy.md",
                "parentId": "parent-1",
                "original": "Refund period is 14 days.",
                "proposed": "Refund period is 30 days.",
                "source": "agent",
                "selectionStart": None,
                "selectionEnd": None,
            }
        ],
        "goal": "Update UK refund policy from 14 days to 30 days.",
        "lexical_anchors": ["14 days", "UK", "refund policy"],
        "semantic_anchors": ["UK refund policy", "refund policy with 14 days period"],
        "anchors": [
            "14 days",
            "UK",
            "refund policy",
            "UK refund policy",
            "refund policy with 14 days period",
        ],
        "constraint": "Only update text that applies to UK refund policy.",
        "node2_search_group_result": {
            "run_summary": {
                "top_k_per_query": 15,
            }
        },
        "node3_non_strong_signal_file_context_expansion_result": {
            "run_summary": {
                "expanded_file_count": 1,
            }
        },
        "node4_file_filtering_result": {
            "run_summary": {
                "promoted_file_count": 1,
            }
        },
        "node5_parent_chunk_constraint_verifier_result": {
            "run_summary": {
                "confirmed_parent_chunk_ref_count": 1,
            }
        },
        "node6_editor_result": {
            "run_summary": {
                "edited_parent_chunk_count": 1,
            }
        },
    }


def test_agentic_modify_forwards_optional_file_ids(monkeypatch):
    captured_state = {"value": None}

    class _ScopedGraph:
        async def ainvoke(self, state):
            captured_state["value"] = state
            return {
                **state,
                "goal": "Scoped update.",
                "lexical_anchors": ["refund"],
                "semantic_anchors": ["refund policy"],
                "anchors": ["refund", "refund policy"],
                "constraint": "None",
                "intention": "edit",
                "proposals": [],
                "node2_search_group_result": {},
                "node3_non_strong_signal_file_context_expansion_result": {},
                "node4_file_filtering_result": {},
                "node5_parent_chunk_constraint_verifier_result": {},
                "node6_editor_result": {},
                "token_prompt_total": 0,
                "token_completion_total": 0,
                "token_total": 0,
                "llm_call_count": 0,
                "error": None,
            }

    monkeypatch.setattr(router_agent, "log_token_usage", lambda **kwargs: None)
    monkeypatch.setattr(router_agent, "_load_retrieval_graph", lambda: _ScopedGraph())
    _patch_collection_scope(monkeypatch, file_ids=["file-a", "file-b", "file-c"])

    response = asyncio.run(
        router_agent.agentic_modify(
            _build_payload(fileIds=["file-a", "file-b"]),
            current_user={"sub": "user-1"},
        )
    )
    payload = response.model_dump()

    assert captured_state["value"] is not None
    assert captured_state["value"]["file_ids"] == ["file-a", "file-b"]
    assert payload["intention"] == "edit"
    assert payload["proposals"] == []


def test_agentic_modify_routes_expose_canonical_and_alias_paths():
    post_routes = {
        route.path: route.endpoint
        for route in router_agent.router.routes
        if isinstance(route, APIRoute) and "POST" in (route.methods or set())
    }
    assert post_routes["/modify"] is router_agent.agentic_modify
    assert post_routes["/v2/modify"] is router_agent.agentic_modify
    assert post_routes["/modify-skills"] is router_agent.agentic_modify_skills
    assert post_routes["/modify-skills-stream"] is router_agent.agentic_modify_skills_stream


def test_agentic_modify_skills_returns_compatible_response(monkeypatch):
    class _FakeSkillResult:
        def model_dump(self):
            return {
                "intention": "edit",
                "proposals": [
                    {
                        "fileId": "file-a",
                        "fileName": "policy.md",
                        "parentId": "parent-a",
                        "original": "Refund is 14 days.",
                        "proposed": "Refund is 30 days.",
                        "source": "agent",
                    }
                ],
                "goal": "Change refund to 30 days.",
                "lexical_anchors": [],
                "semantic_anchors": [],
                "anchors": [],
                "constraint": "None",
                "run_id": "skill-run-1",
                "termination_reason": "finished",
                "tool_call_count": 1,
                "llm_call_count": 2,
                "token_prompt_total": 5,
                "token_completion_total": 6,
                "token_total": 11,
                "skill_runtime_result": {"summary": "done"},
                "coverage_report": {"delegated_files": ["file-a"]},
            }

    captured: dict[str, object] = {}

    async def _fake_runner(**kwargs):
        captured.update(kwargs)
        return _FakeSkillResult()

    monkeypatch.setattr(router_agent, "log_token_usage", lambda **kwargs: None)
    monkeypatch.setattr(router_agent, "_load_agentic_modification_skill_runner", lambda: _fake_runner)
    _patch_collection_scope(monkeypatch, file_ids=["file-a", "file-b"])

    response = asyncio.run(
        router_agent.agentic_modify_skills(
            _build_payload(fileIds=["file-a"]),
            current_user={"sub": "user-1"},
        )
    )
    payload = response.model_dump()

    assert captured["included_file_ids"] == ["file-a"]
    assert payload["intention"] == "edit"
    assert payload["proposals"][0]["fileId"] == "file-a"
    assert payload["proposals"][0]["proposed"] == "Refund is 30 days."


def test_agentic_modify_request_rejects_legacy_instruction_payload():
    with pytest.raises(ValidationError):
        router_agent.AgenticModificationRequest(
            instruction="Change refund from 14 days to 30 days.",
            fileIds=None,
        )
