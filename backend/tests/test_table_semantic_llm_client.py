import sys
import importlib.util
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_requests_stub = types.ModuleType("requests")
_requests_stub.RequestException = Exception
sys.modules.setdefault("requests", _requests_stub)

_LLM_CLIENT_PATH = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "service"
    / "rag"
    / "ingestion"
    / "docling"
    / "table_semantic"
    / "llm_client.py"
)
_SPEC = importlib.util.spec_from_file_location("table_semantic_llm_client", _LLM_CLIENT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

TableSemanticLlmError = _MODULE.TableSemanticLlmError
parse_json_response = _MODULE.parse_json_response
chat_completion = _MODULE.chat_completion


def test_parse_json_response_parses_json_array():
    payload = """
[
  {"slice_index": 0, "summary": "a"},
  {"slice_index": 1, "summary": "b"}
]
""".strip()
    parsed = parse_json_response(payload)
    assert isinstance(parsed, list)
    assert parsed[0]["slice_index"] == 0


def test_parse_json_response_parses_single_quoted_array_wrapper():
    payload = """'[
  {"slice_index": 0, "summary": "a"},
  {"slice_index": 1, "summary": "b"}
]'
""".strip()
    parsed = parse_json_response(payload)
    assert isinstance(parsed, list)
    assert len(parsed) == 2


def test_parse_json_response_parses_escaped_json_string_array():
    payload = "\"[\\n  {\\\"slice_index\\\": 0, \\\"summary\\\": \\\"a\\\"},\\n  {\\\"slice_index\\\": 1, \\\"summary\\\": \\\"b\\\"}\\n]\""
    parsed = parse_json_response(payload)
    assert isinstance(parsed, list)
    assert parsed[1]["summary"] == "b"


def test_parse_json_response_parses_code_fenced_array():
    payload = """```json
[
  {"slice_index": 0, "summary": "a"}
]
```"""
    parsed = parse_json_response(payload)
    assert isinstance(parsed, list)
    assert parsed[0]["summary"] == "a"


def test_parse_json_response_parses_code_fenced_object_wrapped_in_single_quotes():
    payload = """'```json
{
    "type": "entity_list",
    "needs_description": true,
    "col_headers": ["Classes", "Description", "Hit Die"],
    "row_headers": ["Barbarian", "Bard", "Cleric"]
}
```'"""
    parsed = parse_json_response(payload)
    assert isinstance(parsed, dict)
    assert parsed["type"] == "entity_list"
    assert parsed["needs_description"] is True
    assert parsed["col_headers"][0] == "Classes"


def test_parse_json_response_raises_on_non_json():
    with pytest.raises(TableSemanticLlmError):
        parse_json_response("not json at all")


def test_chat_completion_uses_gemini_generate_content_without_auth_header(monkeypatch):
    calls = []

    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": '{"type":"layout","needs_description":false}'}
                            ]
                        }
                    }
                ]
            }

    def _post(endpoint, *, params, headers, json, timeout):
        calls.append(
            {
                "endpoint": endpoint,
                "params": params,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        return _Response()

    monkeypatch.setattr(_MODULE.requests, "post", _post)

    content = chat_completion(
        url="https://generativelanguage.googleapis.com/v1beta",
        api_key="gemini-key",
        model="gemini-3.1-flash-lite-preview",
        system_prompt="system",
        user_prompt="user",
        timeout_s=10,
    )

    assert content == '{"type":"layout","needs_description":false}'
    assert calls[0]["endpoint"].endswith(
        "/models/gemini-3.1-flash-lite-preview:generateContent"
    )
    assert calls[0]["params"] == {"key": "gemini-key"}
    assert "Authorization" not in calls[0]["headers"]
