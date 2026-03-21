import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api import router_agent
from app.service.rag.agent_v2.graph import retrieval_brief_graph as retrieval_brief_graph_module


def _build_payload(**overrides):
    payload = {
        "user_instructions": "Change the refund day from 14 days to 30 days for UK refund policy.",
        "fileIds": None,
    }
    payload.update(overrides)
    return router_agent.AgentV2ModifyRequest(**payload)


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
            "node5_clue_chunk_explorer_result": {
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


def test_agent_v2_modify_rejects_empty_user_instructions():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(router_agent.agent_v2_modify(_build_payload(user_instructions="   ")))

    assert exc.value.status_code == 422


def test_agent_v2_modify_returns_retrieval_brief_and_node_outputs(monkeypatch):
    monkeypatch.setattr(router_agent, "log_token_usage", lambda **kwargs: None)
    monkeypatch.setattr(retrieval_brief_graph_module, "retrieval_brief_graph", _FakeGraph())

    response = asyncio.run(router_agent.agent_v2_modify(_build_payload()))
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
        "node5_clue_chunk_explorer_result": {
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


def test_agent_v2_modify_forwards_optional_file_ids(monkeypatch):
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
                "node5_clue_chunk_explorer_result": {},
                "node6_editor_result": {},
                "token_prompt_total": 0,
                "token_completion_total": 0,
                "token_total": 0,
                "llm_call_count": 0,
                "error": None,
            }

    monkeypatch.setattr(router_agent, "log_token_usage", lambda **kwargs: None)
    monkeypatch.setattr(retrieval_brief_graph_module, "retrieval_brief_graph", _ScopedGraph())

    response = asyncio.run(
        router_agent.agent_v2_modify(
            _build_payload(fileIds=["file-a", "file-b"])
        )
    )
    payload = response.model_dump()

    assert captured_state["value"] is not None
    assert captured_state["value"]["file_ids"] == ["file-a", "file-b"]
    assert payload["intention"] == "edit"
    assert payload["proposals"] == []
