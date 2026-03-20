import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.agent_v2 import retrieval_brief_nodes
from app.service.rag.agent_v2.retrieval_brief_graph import retrieval_brief_graph


def _state(user_instructions: str) -> dict:
    return {
        "user_instructions": user_instructions,
        "run_id": "run-1",
        "goal": "",
        "lexical_anchors": [],
        "semantic_anchors": [],
        "anchors": [],
        "constraint": "None",
        "node2_search_group_result": {},
        "token_prompt_total": 0,
        "token_completion_total": 0,
        "token_total": 0,
        "llm_call_count": 0,
        "error": None,
        "_session": None,
    }


def test_retrieval_brief_node_valid_json(monkeypatch):
    async def _fake_call_llm(*args, **kwargs):
        return (
            (
                '{"goal":"Update UK refund policy from 14 days to 30 days.",' \
                '"lexical_anchors":["14 days","UK","refund policy"],' \
                '"semantic_anchors":["UK refund policy","refund policy with 14 days period"],' \
                '"constraint":"Only update text that applies to UK refund policy."}'
            ),
            {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        )

    monkeypatch.setattr(retrieval_brief_nodes, "_call_llm", _fake_call_llm)

    result = asyncio.run(
        retrieval_brief_nodes.retrieval_brief_extractor_node(
            _state("Change the refund day from 14 days to 30 days for all refund policy under UK.")
        )
    )

    assert result["goal"] == "Update UK refund policy from 14 days to 30 days."
    assert result["lexical_anchors"] == ["14 days", "UK", "refund policy"]
    assert result["semantic_anchors"] == ["UK refund policy", "refund policy with 14 days period"]
    assert result["anchors"] == [
        "14 days",
        "UK",
        "refund policy",
        "UK refund policy",
        "refund policy with 14 days period",
    ]
    assert result["constraint"] == "Only update text that applies to UK refund policy."
    assert result["llm_call_count"] == 1


def test_retrieval_brief_node_empty_constraint_becomes_none(monkeypatch):
    async def _fake_call_llm(*args, **kwargs):
        return (
            '{"goal":"Remove penalty clause.","lexical_anchors":["penalty","invoice terms"],"semantic_anchors":["invoice terms penalty clause"],"constraint":"   "}',
            {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16},
        )

    monkeypatch.setattr(retrieval_brief_nodes, "_call_llm", _fake_call_llm)

    result = asyncio.run(
        retrieval_brief_nodes.retrieval_brief_extractor_node(
            _state("Remove the late payment penalty clause from all invoice terms.")
        )
    )

    assert result["constraint"] == "None"


def test_retrieval_brief_node_malformed_json_falls_back(monkeypatch):
    async def _fake_call_llm(*args, **kwargs):
        return "this is not json", {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16}

    monkeypatch.setattr(retrieval_brief_nodes, "_call_llm", _fake_call_llm)

    result = asyncio.run(
        retrieval_brief_nodes.retrieval_brief_extractor_node(
            _state("Change all date to 3rd March.")
        )
    )

    assert result["goal"]
    assert isinstance(result["lexical_anchors"], list)
    assert isinstance(result["semantic_anchors"], list)
    assert result["anchors"]
    assert result["constraint"] == "None"


def test_retrieval_brief_node_anchor_normalization(monkeypatch):
    async def _fake_call_llm(*args, **kwargs):
        return (
            (
                '{"goal":"Update clause text.",' \
                '"lexical_anchors":[" UK ","uk","","refund policy","Refund Policy",123],' \
                '"semantic_anchors":["uk refund policy","UK refund policy"],' \
                '"constraint":"None"}'
            ),
            {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16},
        )

    monkeypatch.setattr(retrieval_brief_nodes, "_call_llm", _fake_call_llm)

    result = asyncio.run(
        retrieval_brief_nodes.retrieval_brief_extractor_node(
            _state("Change refund policy wording under UK.")
        )
    )

    assert result["lexical_anchors"] == ["UK", "refund policy"]
    assert result["semantic_anchors"] == ["uk refund policy"]


def test_retrieval_brief_graph_runs_two_nodes(monkeypatch):
    async def _fake_call_llm(*args, **kwargs):
        return (
            '{"goal":"Remove late payment penalty clause.","lexical_anchors":["late payment penalty","invoice terms"],"semantic_anchors":["invoice terms with penalty clause"],"constraint":"Only update text that defines invoice payment terms."}',
            {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        )

    async def _fake_lexical_search(*args, **kwargs):
        return []

    async def _fake_semantic_search(*args, **kwargs):
        return []

    monkeypatch.setattr(retrieval_brief_nodes, "_call_llm", _fake_call_llm)
    monkeypatch.setattr(retrieval_brief_nodes, "_run_lexical_search", _fake_lexical_search)
    monkeypatch.setattr(retrieval_brief_nodes, "_run_semantic_search", _fake_semantic_search)
    monkeypatch.setattr(retrieval_brief_nodes, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(
        retrieval_brief_graph.ainvoke(
            _state("Remove the late payment penalty clause from all invoice terms.")
        )
    )

    assert result["goal"] == "Remove late payment penalty clause."
    assert result["lexical_anchors"] == ["late payment penalty", "invoice terms"]
    assert result["semantic_anchors"] == ["invoice terms with penalty clause"]
    assert result["constraint"] == "Only update text that defines invoice payment terms."
    assert isinstance(result["node2_search_group_result"], dict)
    assert result["node2_search_group_result"]["run_summary"]["top_k_per_query"] == 15