import asyncio
import json
import os
import sys
from pathlib import Path

from langchain_core.documents import Document

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.agent_v2.nodes import search_and_group_node
from app.service.rag.agent_v2.services import vector_search


def _base_state(*, lexical_anchors=None, semantic_anchors=None):
    return {
        "user_instructions": "",
        "run_id": "run-node2",
        "goal": "",
        "lexical_anchors": lexical_anchors or [],
        "semantic_anchors": semantic_anchors or [],
        "anchors": [],
        "constraint": "None",
        "node2_search_group_result": {},
        "node3_non_strong_signal_file_context_expansion_result": {},
        "node4_file_filtering_result": {},
        "token_prompt_total": 0,
        "token_completion_total": 0,
        "token_total": 0,
        "llm_call_count": 0,
        "error": None,
        "_session": None,
        "_retrieval_cache": {},
    }


def _anchors_from_env() -> tuple[list[str], list[str]]:
    """
    Load fake anchors from env for real-backend node-2 testing.

    Expected format:
    - AGENT_V2_NODE2_LEXICAL='["$50","minimum order amount"]'
    - AGENT_V2_NODE2_SEMANTIC='["wholesale minimum order"]'
    """
    lexical_raw = os.getenv("AGENT_V2_NODE2_LEXICAL", "[\"$50\"]")
    semantic_raw = os.getenv(
        "AGENT_V2_NODE2_SEMANTIC",
        "[\"wholesale customer minimum order\"]",
    )

    try:
        lexical = json.loads(lexical_raw)
        semantic = json.loads(semantic_raw)
    except json.JSONDecodeError as error:
        raise AssertionError(f"Invalid JSON anchors in env vars: {error}") from error

    if not isinstance(lexical, list) or not isinstance(semantic, list):
        raise AssertionError("AGENT_V2_NODE2_LEXICAL and AGENT_V2_NODE2_SEMANTIC must be JSON arrays.")

    lexical_anchors = [str(item).strip() for item in lexical if str(item).strip()]
    semantic_anchors = [str(item).strip() for item in semantic if str(item).strip()]
    return lexical_anchors, semantic_anchors


def test_node2_mixed_hits_produce_strong_chunk_and_strong_file(monkeypatch):
    async def _fake_lexical_search(query: str, top_k: int):
        return [
            {
                "_id": "child-1",
                "metadata": {"file_metadata": {"file_id": "file-1", "file_name": "a.md"}},
                "lexical_score": 0.9,
            }
        ]

    async def _fake_semantic_search(query: str, top_k: int):
        return [
            (
                Document(
                    page_content="x",
                    metadata={
                        "child_chunk_id": "child-1",
                        "file_metadata": {"file_id": "file-1", "file_name": "a.md"},
                    },
                ),
                0.92,
            )
        ]

    monkeypatch.setattr(vector_search, "_run_lexical_search", _fake_lexical_search)
    monkeypatch.setattr(vector_search, "_run_semantic_search", _fake_semantic_search)
    monkeypatch.setattr(search_and_group_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(
        search_and_group_node.search_and_group_node(
            _base_state(lexical_anchors=["$50"], semantic_anchors=["minimum order"])
        )
    )

    node2 = result["node2_search_group_result"]
    child = node2["children"][0]
    file_item = node2["files"][0]

    assert child["strong_signal_chunk"] is True
    assert file_item["strong_signal_file"] is True


def test_node2_file_with_both_sources_is_strong_even_with_low_semantic_score(monkeypatch):
    async def _fake_lexical_search(query: str, top_k: int):
        return [
            {
                "_id": "child-1",
                "metadata": {"file_metadata": {"file_id": "file-1", "file_name": "a.md"}},
            }
        ]

    async def _fake_semantic_search(query: str, top_k: int):
        return [
            (
                Document(
                    page_content="x",
                    metadata={
                        "child_chunk_id": "child-1",
                        "file_metadata": {"file_id": "file-1", "file_name": "a.md"},
                    },
                ),
                0.75,
            )
        ]

    monkeypatch.setattr(vector_search, "_run_lexical_search", _fake_lexical_search)
    monkeypatch.setattr(vector_search, "_run_semantic_search", _fake_semantic_search)
    monkeypatch.setattr(search_and_group_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(
        search_and_group_node.search_and_group_node(
            _base_state(lexical_anchors=["$50"], semantic_anchors=["minimum order"])
        )
    )

    node2 = result["node2_search_group_result"]
    assert node2["files"][0]["strong_signal_file"] is True


def test_node2_file_with_only_lexical_hits_is_not_strong(monkeypatch):
    async def _fake_lexical_search(query: str, top_k: int):
        return [
            {
                "_id": "child-1",
                "metadata": {"file_metadata": {"file_id": "file-1", "file_name": "a.md"}},
            }
        ]

    async def _fake_semantic_search(query: str, top_k: int):
        return []

    monkeypatch.setattr(vector_search, "_run_lexical_search", _fake_lexical_search)
    monkeypatch.setattr(vector_search, "_run_semantic_search", _fake_semantic_search)
    monkeypatch.setattr(search_and_group_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(
        search_and_group_node.search_and_group_node(
            _base_state(lexical_anchors=["$50"], semantic_anchors=["minimum order"])
        )
    )

    node2 = result["node2_search_group_result"]
    assert node2["files"][0]["strong_signal_file"] is False


def test_node2_file_with_only_semantic_hits_is_not_strong(monkeypatch):
    async def _fake_lexical_search(query: str, top_k: int):
        return []

    async def _fake_semantic_search(query: str, top_k: int):
        return [
            (
                Document(
                    page_content="x",
                    metadata={
                        "child_chunk_id": "child-1",
                        "file_metadata": {"file_id": "file-1", "file_name": "a.md"},
                    },
                ),
                0.6,
            )
        ]

    monkeypatch.setattr(vector_search, "_run_lexical_search", _fake_lexical_search)
    monkeypatch.setattr(vector_search, "_run_semantic_search", _fake_semantic_search)
    monkeypatch.setattr(search_and_group_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(
        search_and_group_node.search_and_group_node(
            _base_state(lexical_anchors=["$50"], semantic_anchors=["minimum order"])
        )
    )

    node2 = result["node2_search_group_result"]
    assert node2["files"][0]["strong_signal_file"] is False


def test_node2_high_signal_chunk_auto_marks_associated_file(monkeypatch):
    async def _fake_lexical_search(query: str, top_k: int):
        # File-1 has lexical hit for child-1; File-2 lexical-only.
        return [
            {
                "_id": "child-1",
                "metadata": {"file_metadata": {"file_id": "file-1", "file_name": "a.md"}},
            },
            {
                "_id": "child-2",
                "metadata": {"file_metadata": {"file_id": "file-2", "file_name": "b.md"}},
            },
        ]

    async def _fake_semantic_search(query: str, top_k: int):
        # Semantic hit only for child-1, making it a strong chunk.
        return [
            (
                Document(
                    page_content="x",
                    metadata={
                        "child_chunk_id": "child-1",
                        "file_metadata": {"file_id": "file-1", "file_name": "a.md"},
                    },
                ),
                0.2,
            )
        ]

    monkeypatch.setattr(vector_search, "_run_lexical_search", _fake_lexical_search)
    monkeypatch.setattr(vector_search, "_run_semantic_search", _fake_semantic_search)
    monkeypatch.setattr(search_and_group_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(
        search_and_group_node.search_and_group_node(
            _base_state(lexical_anchors=["x"], semantic_anchors=["y"])
        )
    )

    node2 = result["node2_search_group_result"]
    files_by_id = {item["file_id"]: item for item in node2["files"]}
    assert files_by_id["file-1"]["strong_signal_file"] is True
    assert files_by_id["file-2"]["strong_signal_file"] is False
    assert node2["run_summary"]["strong_signal_files"] != "none"


def test_node2_missing_child_id_fails_node(monkeypatch):
    async def _fake_lexical_search(query: str, top_k: int):
        return []

    async def _fake_semantic_search(query: str, top_k: int):
        return [
            (
                Document(
                    page_content="x",
                    metadata={
                        "file_metadata": {"file_id": "file-1", "file_name": "a.md"},
                    },
                ),
                0.9,
            )
        ]

    monkeypatch.setattr(vector_search, "_run_lexical_search", _fake_lexical_search)
    monkeypatch.setattr(vector_search, "_run_semantic_search", _fake_semantic_search)
    monkeypatch.setattr(search_and_group_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(
        search_and_group_node.search_and_group_node(
            _base_state(lexical_anchors=["$50"], semantic_anchors=["minimum order"])
        )
    )

    assert "error" in result
    assert "Missing child_chunk_id" in result["error"]


def test_node2_real_backend_search_with_fake_anchors_from_env(monkeypatch):
    """
    Optional integration test:
    - Runs real lexical + semantic backend search (no search monkeypatch).
    - Uses fake anchors provided via env vars.

    Enable with:
      $env:AGENT_V2_NODE2_INTEGRATION='1'
      $env:AGENT_V2_NODE2_LEXICAL='["$50","minimum order amount","wholesale customers"]'
      $env:AGENT_V2_NODE2_SEMANTIC='["wholesale customer minimum order","minimum order requirement for wholesale"]'
      python -m pytest backend/tests/test_agent_v2_search_group_node.py -k real_backend -q
    """
    if os.getenv("AGENT_V2_NODE2_INTEGRATION", "").strip() != "1":
        # Keep default test runs deterministic and offline-friendly.
        return

    lexical_anchors, semantic_anchors = _anchors_from_env()
    assert lexical_anchors or semantic_anchors, "At least one lexical or semantic anchor is required."

    monkeypatch.setattr(search_and_group_node, "log_modification_agent_search_group", lambda **kwargs: None)

    result = asyncio.run(
        search_and_group_node.search_and_group_node(
            _base_state(lexical_anchors=lexical_anchors, semantic_anchors=semantic_anchors)
        )
    )

    assert "error" not in result, result.get("error")
    node2 = result["node2_search_group_result"]
    assert node2["queries"]["lexical_anchors"] == lexical_anchors
    assert node2["queries"]["semantic_anchors"] == semantic_anchors
    assert node2["run_summary"]["top_k_per_query"] == 15
