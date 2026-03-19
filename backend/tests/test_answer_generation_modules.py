"""Unit tests for refactored answer generation modules."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.service.rag.retrieval import answer_generator as answer_generator_facade
from app.service.rag.retrieval.answer_generation.citations import (
    append_or_replace_sources_suffix,
    collect_source_file_names,
)
from app.service.rag.retrieval.answer_generation.config import (
    load_answer_generator_config,
)
from app.service.rag.retrieval.answer_generation.context_normalizer import (
    build_llm_context_docs,
    build_llm_context_payload,
    normalize_rag_docs,
)
from app.service.rag.retrieval.answer_generation.http_client import post_json
from app.service.rag.retrieval.answer_generation.models import AnswerGeneratorConfig
from app.service.rag.retrieval.answer_generation.orchestration import _resolve_provider
from app.service.rag.retrieval.answer_generation.providers.ollama_provider import (
    coerce_ollama_response_dict,
    generate_via_ollama,
)
from app.service.rag.retrieval.answer_generation.providers.openrouter_provider import (
    generate_via_openrouter,
)


class _FakeResponse:
    def __init__(self, status: int, payload=None, text: str = "") -> None:
        self.status = status
        self._payload = payload
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._text

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response

    def post(self, *args, **kwargs):
        return self._response


def test_load_config_preserves_ollama_url_without_resolve(monkeypatch):
    monkeypatch.setenv("ANSWER_GENERATOR_LLM_PROVIDER", "OLLAMA")
    monkeypatch.setenv("ANSWER_GENERATOR_TIMEOUT_S", "30")
    monkeypatch.setenv("OLLAMA_ANSWER_GENERATOR_LLM_URL", "https://example.com/custom/endpoint")

    cfg = load_answer_generator_config()
    assert cfg.url == "https://example.com/custom/endpoint"


def test_load_config_ollama_swap_to_ram_toggle(monkeypatch):
    monkeypatch.setenv("ANSWER_GENERATOR_LLM_PROVIDER", "OLLAMA")
    monkeypatch.setenv("ANSWER_GENERATOR_TIMEOUT_S", "30")
    monkeypatch.setenv("OLLAMA_ANSWER_GENERATOR_LLM_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("OLLAMA_SWAP_TO_RAM", "true")

    cfg = load_answer_generator_config()
    assert cfg.ollama_swap_to_ram is True


def test_load_config_ollama_swap_to_ram_default_false(monkeypatch):
    monkeypatch.setenv("ANSWER_GENERATOR_LLM_PROVIDER", "OLLAMA")
    monkeypatch.setenv("ANSWER_GENERATOR_TIMEOUT_S", "30")
    monkeypatch.setenv("OLLAMA_ANSWER_GENERATOR_LLM_MODEL", "qwen2.5:7b")
    monkeypatch.delenv("OLLAMA_SWAP_TO_RAM", raising=False)

    cfg = load_answer_generator_config()
    assert cfg.ollama_swap_to_ram is False


def test_load_config_timeout_validation(monkeypatch):
    monkeypatch.setenv("ANSWER_GENERATOR_TIMEOUT_S", "not_a_number")
    with pytest.raises(RuntimeError, match="Invalid answer generator timeout value"):
        load_answer_generator_config()


def test_load_config_beam_uses_beam_url(monkeypatch):
    monkeypatch.setenv("ANSWER_GENERATOR_LLM_PROVIDER", "BEAM")
    monkeypatch.setenv("ANSWER_GENERATOR_TIMEOUT_S", "30")
    monkeypatch.setenv("BEAM_ANSWER_GENERATOR_LLM_URL", "https://beam-host/custom/generate")
    monkeypatch.setenv("BEAM_ANSWER_GENERATOR_LLM_KEY", "beam-token")
    monkeypatch.delenv("LOCAL_ANSWER_GENERATOR_LLM_URL", raising=False)
    monkeypatch.delenv("OLLAMA_ANSWER_GENERATOR_LLM_URL", raising=False)

    cfg = load_answer_generator_config()
    assert cfg.provider == "BEAM"
    assert cfg.url == "https://beam-host/custom/generate"
    assert cfg.api_key == "beam-token"


def test_load_config_beam_url_required(monkeypatch):
    monkeypatch.setenv("ANSWER_GENERATOR_LLM_PROVIDER", "BEAM")
    monkeypatch.setenv("ANSWER_GENERATOR_TIMEOUT_S", "30")
    monkeypatch.delenv("BEAM_ANSWER_GENERATOR_LLM_URL", raising=False)

    # BEAM is deterministic now: URL fallbacks must be ignored.
    monkeypatch.setenv("LOCAL_ANSWER_GENERATOR_LLM_URL", "local-host:11434")
    monkeypatch.setenv("OLLAMA_ANSWER_GENERATOR_LLM_URL", "ollama-host:11434")
    monkeypatch.setenv("BEAM_ANSWER_GENERATOR_LLM_KEY", "beam-token")

    with pytest.raises(RuntimeError, match="BEAM_ANSWER_GENERATOR_LLM_URL is required"):
        load_answer_generator_config()


def test_load_config_beam_api_key_required(monkeypatch):
    monkeypatch.setenv("ANSWER_GENERATOR_LLM_PROVIDER", "BEAM")
    monkeypatch.setenv("ANSWER_GENERATOR_TIMEOUT_S", "30")
    monkeypatch.setenv("BEAM_ANSWER_GENERATOR_LLM_URL", "https://beam-host/custom/generate")
    monkeypatch.delenv("BEAM_ANSWER_GENERATOR_LLM_KEY", raising=False)
    monkeypatch.delenv("LOCAL_ANSWER_GENERATOR_LLM_KEY", raising=False)

    with pytest.raises(RuntimeError, match="BEAM_ANSWER_GENERATOR_LLM_KEY is required"):
        load_answer_generator_config()


def test_normalize_rag_docs_dict_input():
    docs = normalize_rag_docs(
        [
            {"id": "a", "metadata": {"file_metadata": {"file_name": "file_a.pdf"}}, "page_content": "A"},
            {"id": "b", "metadata": {}, "page_content": "B"},
        ]
    )

    assert docs[0]["metadata"]["file_name"] == "file_a.pdf"
    assert docs[1]["page_content"] == "B"


def test_normalize_rag_docs_non_dict_raises():
    with pytest.raises(RuntimeError, match="expected dict, got str"):
        normalize_rag_docs(
            [
                {"id": "a", "metadata": {}, "page_content": "A"},
                "raw chunk",
            ]
        )


def test_build_llm_context_docs_strips_internal_fields():
    docs = [
        {
            "id": "a",
            "metadata": {"file_name": "file_a.pdf", "parent_chunk_number": 3},
            "page_content": "A",
            "type": "Document",
        },
        {
            "id": "b",
            "metadata": {},
            "page_content": "B",
            "type": "Document",
        },
    ]

    llm_docs = build_llm_context_docs(docs)

    assert llm_docs == [
        {"file_name": "file_a.pdf", "page_content": "A"},
        {"page_content": "B"},
    ]
    assert "id" not in llm_docs[0]
    assert "metadata" not in llm_docs[0]
    assert "parent_chunk_number" not in llm_docs[0]
    assert "type" not in llm_docs[0]


def test_build_llm_context_payload_uses_numbered_blocks():
    docs = [
        {
            "id": "a",
            "metadata": {"file_name": "COMP2005_2026_CWBrief (1).pdf", "parent_chunk_number": 3},
            "page_content": "First line",
            "type": "Document",
        },
        {
            "id": "b",
            "metadata": {},
            "page_content": "Second line",
            "type": "Document",
        },
    ]

    payload = build_llm_context_payload(docs)

    assert "[1]" in payload
    assert "file_name: COMP2005_2026_CWBrief (1).pdf" in payload
    assert 'page_content: "First line"' in payload
    assert "[2]" in payload
    assert "file_name: filename unknown" in payload
    assert 'page_content: "Second line"' in payload
    assert "parent_chunk_number" not in payload
    assert "metadata" not in payload
    assert "'id'" not in payload
    assert "'type'" not in payload


def test_citations_append_or_replace_suffix():
    answer = append_or_replace_sources_suffix("Result text", ["a.pdf", "b.pdf"])
    assert answer.endswith("(Sources: a.pdf, b.pdf)")

    replaced = append_or_replace_sources_suffix("Result text\n(Sources: old.pdf)", ["new.pdf"])
    assert replaced.endswith("(Sources: new.pdf)")


def test_collect_source_file_names_dedup_order():
    docs = [
        {"id": None, "metadata": {"file_name": "a.pdf"}, "page_content": "A", "type": "Document"},
        {"id": None, "metadata": {"file_name": "b.pdf"}, "page_content": "B", "type": "Document"},
        {"id": None, "metadata": {"file_name": "a.pdf"}, "page_content": "C", "type": "Document"},
    ]
    assert collect_source_file_names(docs) == ["a.pdf", "b.pdf"]


def test_post_json_non_200_status_error():
    session = _FakeSession(_FakeResponse(status=500, text="boom"))
    with pytest.raises(RuntimeError, match="Test error \(500\): boom"):
        asyncio.run(
            post_json(
                session=session,
                url="http://example.com",
                payload={},
                headers={},
                timeout_s=1,
                error_prefix="Test error",
            )
        )


def test_post_json_non_object_response_error():
    session = _FakeSession(_FakeResponse(status=200, payload=[1, 2, 3]))
    with pytest.raises(RuntimeError, match="expected JSON object response"):
        asyncio.run(
            post_json(
                session=session,
                url="http://example.com",
                payload={},
                headers={},
                timeout_s=1,
                error_prefix="Test error",
            )
        )


def test_coerce_ollama_response_dict_from_attr():
    obj = SimpleNamespace(response="hello")
    assert coerce_ollama_response_dict(obj, "prefix") == {"response": "hello"}


def test_generate_via_ollama_missing_model_raises():
    cfg = AnswerGeneratorConfig(
        provider="OLLAMA",
        timeout_s=10,
        url=None,
        model=None,
        api_key=None,
    )
    with pytest.raises(RuntimeError, match="model is missing"):
        asyncio.run(generate_via_ollama(_FakeSession(_FakeResponse(200, {})), cfg, [], "hi"))

def test_generate_via_openrouter_missing_key_raises():
    cfg = AnswerGeneratorConfig(
        provider="OPENROUTER",
        timeout_s=10,
        url="https://openrouter.ai/api/v1/chat/completions",
        model="model",
        api_key=None,
    )
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        asyncio.run(generate_via_openrouter(_FakeSession(_FakeResponse(200, {})), cfg, [], "hi"))


def test_generate_via_openrouter_parses_and_appends_sources(monkeypatch):
    cfg = AnswerGeneratorConfig(
        provider="OPENROUTER",
        timeout_s=10,
        url="https://openrouter.ai/api/v1/chat/completions",
        model="model",
        api_key="token",
    )

    captured: dict[str, object] = {}

    async def _fake_post_json(**kwargs):
        captured["payload"] = kwargs.get("payload")
        return {"choices": [{"message": {"content": "Core answer"}}]}

    module = sys.modules[
        "app.service.rag.retrieval.answer_generation.providers.openrouter_provider"
    ]
    monkeypatch.setattr(module, "post_json", _fake_post_json)

    docs = [
        {"id": None, "metadata": {"file_name": "x.pdf"}, "page_content": "ctx", "type": "Document"}
    ]
    output = asyncio.run(generate_via_openrouter(_FakeSession(_FakeResponse(200, {})), cfg, docs, "q"))
    assert "Core answer" in output
    assert "(Sources: x.pdf)" in output
    payload = captured.get("payload")
    assert isinstance(payload, dict)
    messages = payload.get("messages")
    assert isinstance(messages, list) and len(messages) == 2
    user_content = messages[1]["content"]
    assert "<CONTEXT>" in user_content
    assert "</CONTEXT>" in user_content
    assert "<CONTEXT_TOON>" not in user_content
    assert "<CONTEXT_JSON>" not in user_content
    assert "[1]" in user_content
    assert "file_name: x.pdf" in user_content
    assert 'page_content: "ctx"' in user_content
    assert "file_name" in user_content
    assert "page_content" in user_content
    assert "parent_chunk_number" not in user_content
    assert "'id'" not in user_content
    assert "'type'" not in user_content
    assert "metadata" not in user_content


def test_resolve_provider_alias_and_invalid():
    ollama_cfg = AnswerGeneratorConfig(
        provider="BEAM",
        timeout_s=10,
        url=None,
        model="m",
        api_key="k",
    )
    assert _resolve_provider(ollama_cfg).__class__.__name__ == "OllamaAnswerProvider"

    bad_cfg = AnswerGeneratorConfig(
        provider="UNKNOWN",
        timeout_s=10,
        url=None,
        model="m",
        api_key=None,
    )
    with pytest.raises(RuntimeError, match="Invalid ANSWER_GENERATOR_LLM_PROVIDER"):
        _resolve_provider(bad_cfg)


def test_facade_generate_answer_api_delegates(monkeypatch):
    async def _fake_generate_answer(rag_docs, user_query):
        return f"ok:{user_query}:{len(rag_docs)}"

    from app.service.rag.retrieval.answer_generation import orchestration

    monkeypatch.setattr(orchestration, "generate_answer", _fake_generate_answer)
    result = asyncio.run(
        answer_generator_facade.generate_answer_api(
            [{"id": None, "metadata": {}, "page_content": "ctx", "type": "Document"}],
            "query",
        )
    )
    assert result == "ok:query:1"
