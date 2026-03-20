import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api import router_agent
from app.service.rag.agent_v2 import retrieval_brief_graph as retrieval_brief_graph_module


def _build_payload(**overrides):
    payload = {
        "user_instructions": "Change the refund day from 14 days to 30 days for UK refund policy.",
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
            "node2_search_group_result": {
                "run_summary": {
                    "top_k_per_query": 15,
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


def test_agent_v2_modify_returns_retrieval_brief_and_node2_output(monkeypatch):
    monkeypatch.setattr(router_agent, "log_token_usage", lambda **kwargs: None)
    monkeypatch.setattr(retrieval_brief_graph_module, "retrieval_brief_graph", _FakeGraph())

    response = asyncio.run(router_agent.agent_v2_modify(_build_payload()))
    payload = response.model_dump()

    assert payload == {
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
    }