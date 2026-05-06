"""Unit tests for the query refiner module."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

fake_aiohttp = types.ModuleType("aiohttp")
fake_aiohttp.ClientError = Exception
fake_aiohttp.ClientTimeout = lambda total: {"total": total}
fake_aiohttp.ClientSession = object
sys.modules.setdefault("aiohttp", fake_aiohttp)

from app.service.rag.retrieval import query_refiner


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


class _FakeClientSession:
    def __init__(self, *, response: _FakeResponse, capture: dict[str, object]) -> None:
        self._response = response
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json, headers):
        self._capture["url"] = url
        self._capture["json"] = json
        self._capture["headers"] = headers
        return self._response


def test_resolve_runtime_config_prefers_canonical_llm_envs(monkeypatch):
    monkeypatch.setenv("LLM_API_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_API_KEY", "canonical-key")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LLM_THINKING", "disabled")
    monkeypatch.setenv("BEAM_REFINE_LLM_URL", "https://legacy-refiner.example/v1/chat/completions")
    monkeypatch.setenv("BEAM_REFINE_LLM_KEY", "legacy-refiner-key")

    url, api_key, model, thinking = query_refiner._resolve_runtime_config()

    assert url == "https://api.deepseek.com/chat/completions"
    assert api_key == "canonical-key"
    assert model == "deepseek-v4-flash"
    assert thinking == "disabled"


def test_refine_query_sends_chat_completion_payload(monkeypatch):
    captured: dict[str, object] = {}
    fake_response = _FakeResponse(
        status=200,
        payload={"choices": [{"message": {"content": "machine learning definition"}}]},
    )

    monkeypatch.setenv("LLM_API_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_API_KEY", "canonical-key")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LLM_THINKING", "disabled")
    monkeypatch.setattr(
        query_refiner.aiohttp,
        "ClientSession",
        lambda: _FakeClientSession(response=fake_response, capture=captured),
    )

    result = asyncio.run(query_refiner.refine_query("What is machine learning?"))

    assert result == "machine learning definition"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["headers"] == {
        "Authorization": "Bearer canonical-key",
        "Content-Type": "application/json",
    }
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["temperature"] == 0
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1] == {"role": "user", "content": "What is machine learning?"}


def test_refine_query_raises_on_api_error(monkeypatch):
    fake_response = _FakeResponse(status=500, text="boom")
    monkeypatch.setenv("LLM_API_KEY", "canonical-key")
    monkeypatch.setattr(
        query_refiner.aiohttp,
        "ClientSession",
        lambda: _FakeClientSession(response=fake_response, capture={}),
    )

    with pytest.raises(ValueError, match="LLM request failed \\(500\\): boom"):
        asyncio.run(query_refiner.refine_query("query"))


def test_refine_query_requires_api_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("BEAM_REFINE_LLM_KEY", raising=False)

    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        asyncio.run(query_refiner.refine_query("query"))


async def run_all_tests():
    """Legacy helper retained for the manual query test runner."""

    return True
