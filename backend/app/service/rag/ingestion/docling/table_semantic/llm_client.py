"""
OpenAI-compatible LLM client for semantic table ingestion.
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests


class TableSemanticLlmError(RuntimeError):
    pass


def _is_gemini_endpoint(raw_url: str) -> bool:
    """Return True when URL points to Google Gemini generateContent style APIs."""

    lowered = (raw_url or "").strip().lower()
    return (
        "generativelanguage.googleapis.com" in lowered
        or ":generatecontent" in lowered
        or "/models/" in lowered
    )


def _normalize_chat_completions_url(raw_url: str) -> str:
    """
    Normalize the base URL for the chat completions endpoint, ensuring it ends with '/chat/completions' and raising an error if the resulting URL is empty.
    """
    url = (raw_url or "").strip().rstrip("/")
    if not url:
        raise TableSemanticLlmError("TABLE_SEMANTIC_LLM_URL is not configured.")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/chat/completions"


def _normalize_gemini_generate_content_url(raw_url: str, model: str) -> str:
    """
    Normalize Gemini URL into `.../models/{model}:generateContent`.

    Handles either a base URL (`.../v1beta`) or an already model-scoped URL.
    """

    url = (raw_url or "").strip().rstrip("/")
    if not url:
        raise TableSemanticLlmError("TABLE_SEMANTIC_LLM_URL is not configured.")
    if not model:
        raise TableSemanticLlmError("Semantic table model is empty.")

    # Recover from OpenAI-normalized suffixes if config used shared resolver.
    for suffix in ("/chat/completions", "/v1/chat/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            break

    if "{model}" in url:
        return url.format(model=model)
    if url.endswith(":generateContent"):
        return url
    if url.endswith("/models"):
        return f"{url}/{model}:generateContent"
    if "/models/" in url:
        if url.endswith(model):
            return f"{url}:generateContent"
        return f"{url}/{model}:generateContent"
    return f"{url}/models/{model}:generateContent"


def _extract_text_content(payload: dict[str, Any]) -> str:
    """
    Extract the text content from the first choice of the LLM response payload, with error handling to provide clear error messages when the expected structure is not present or the content is empty.
    """
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise TableSemanticLlmError("LLM response has no choices.")
    first = choices[0]
    if not isinstance(first, dict):
        raise TableSemanticLlmError("LLM response choice is invalid.")
    message = first.get("message")
    if not isinstance(message, dict):
        # Some providers place text directly on choice.
        direct_text = first.get("text")
        if isinstance(direct_text, str) and direct_text.strip():
            return direct_text.strip()
        raise TableSemanticLlmError("LLM response choice message is invalid.")
    content = message.get("content")
    if isinstance(content, str):
        text = content.strip()
        if text:
            return text
    elif isinstance(content, list):
        # OpenAI-compatible providers may return content as parts:
        # [{"type":"text","text":"..."}]
        part_texts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            candidate = part.get("text")
            if isinstance(candidate, str):
                normalized = candidate.strip()
                if normalized:
                    part_texts.append(normalized)
        if part_texts:
            return "\n".join(part_texts)

    finish_reason = first.get("finish_reason")
    refusal = message.get("refusal")
    provider_error = payload.get("error")
    usage = payload.get("usage")
    preview = json.dumps(
        {
            "finish_reason": finish_reason,
            "refusal": refusal,
            "error": provider_error,
            "usage": usage,
        },
        ensure_ascii=False,
    )[:500]
    raise TableSemanticLlmError(f"LLM response content is empty. details={preview}")


def _extract_text_content_gemini(payload: dict[str, Any]) -> str:
    """Extract text content from Gemini generateContent response envelope."""

    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise TableSemanticLlmError("Gemini response has no candidates.")
    first = candidates[0]
    if not isinstance(first, dict):
        raise TableSemanticLlmError("Gemini response candidate is invalid.")
    content = first.get("content")
    if not isinstance(content, dict):
        raise TableSemanticLlmError("Gemini response candidate content is invalid.")
    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        raise TableSemanticLlmError("Gemini response has no content parts.")

    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
            normalized = text.strip()
            if normalized:
                texts.append(normalized)
    if not texts:
        raise TableSemanticLlmError("Gemini response content is empty.")
    return "\n".join(texts)


def _extract_json_substring(text: str) -> str:
    """
    Extract the first complete top-level JSON value (object or array) from
    potentially wrapped model output.

    This uses bracket matching with string/escape awareness, which is safer
    than naive first/last bracket slicing when JSON objects contain arrays.
    """
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    start_idx = -1
    stack: list[str] = []
    in_string = False
    escaped = False

    for idx, ch in enumerate(stripped):
        if in_string:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch in "{[":
            if start_idx < 0:
                start_idx = idx
            stack.append(ch)
            continue

        if ch in "}]":
            if not stack:
                continue
            opening = stack[-1]
            if (opening == "{" and ch == "}") or (opening == "[" and ch == "]"):
                stack.pop()
                if start_idx >= 0 and not stack:
                    return stripped[start_idx : idx + 1]

    return stripped


def _json_loads_maybe_nested(candidate: str, *, max_depth: int = 3) -> Any:
    """
    Attempt to parse the candidate string as JSON, and if the result is itself a string, attempt to parse it again, up to a maximum nesting depth. This handles cases where the LLM may return a JSON string that contains an escaped JSON object or array.
    """
    current = (candidate or "").strip()
    parsed: Any = current
    for _ in range(max_depth):
        parsed = json.loads(current)
        if isinstance(parsed, str):
            current = parsed.strip()
            continue
        return parsed
    return parsed


def parse_json_response(text: str) -> Any:
    """Parse the LLM response text content as JSON, with error handling to provide clear error messages when the content is not valid JSON or does not contain a JSON substring."""
    raw = (text or "").strip()

    # Try direct parse first to handle payloads that are valid JSON strings
    # containing escaped JSON objects/arrays.
    try:
        return _json_loads_maybe_nested(raw)
    except json.JSONDecodeError as exc:
        direct_exc = exc

    # Fallback: extract best JSON-looking substring from mixed prose/wrappers.
    candidate = _extract_json_substring(raw)
    try:
        return _json_loads_maybe_nested(candidate)
    except json.JSONDecodeError as exc:
        raise TableSemanticLlmError(
            "LLM returned non-JSON payload for structured stage: "
            f"{text[:300]!r}; direct_parse_error={direct_exc}"
        ) from exc


def chat_completion(
    *,
    url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout_s: float,
) -> str:
    """Call the LLM chat completions endpoint and return the text content, with error handling for common issues like non-200 response or invalid JSON."""
    if not api_key:
        raise TableSemanticLlmError(
            "TABLE_SEMANTIC_LLM_API_KEY is required for semantic table ingestion."
        )
    if not model:
        raise TableSemanticLlmError("Semantic table model is empty.")

    if _is_gemini_endpoint(url):
        endpoint = _normalize_gemini_generate_content_url(url, model)
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": system_prompt.strip()},
                        {"text": user_prompt.strip()},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0,
            },
        }
        try:
            response = requests.post(
                endpoint,
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=timeout_s,
            )
        except requests.RequestException as exc:
            raise TableSemanticLlmError(f"Semantic table LLM request failed: {exc}") from exc

        if response.status_code != 200:
            body_preview = (response.text or "")[:500]
            raise TableSemanticLlmError(
                f"Semantic table LLM API error ({response.status_code}): {body_preview}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise TableSemanticLlmError(
                "Semantic table LLM response is not valid JSON envelope."
            ) from exc
        if not isinstance(data, dict):
            raise TableSemanticLlmError(
                "Semantic table LLM response envelope is invalid."
            )
        return _extract_text_content_gemini(data)

    endpoint = _normalize_chat_completions_url(url)
    auth_key = api_key
    # If a Gemini-style key was provided for an OpenAI-compatible endpoint,
    # prefer OPENROUTER_API_KEY fallback when available.
    if auth_key.startswith("AIza"):
        fallback_openrouter_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
        if fallback_openrouter_key:
            auth_key = fallback_openrouter_key

    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {
        "Authorization": f"Bearer {auth_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=timeout_s,
        )
    except requests.RequestException as exc:
        raise TableSemanticLlmError(f"Semantic table LLM request failed: {exc}") from exc

    if response.status_code != 200:
        body_preview = (response.text or "")[:500]
        raise TableSemanticLlmError(
            f"Semantic table LLM API error ({response.status_code}): {body_preview}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise TableSemanticLlmError(
            "Semantic table LLM response is not valid JSON envelope."
        ) from exc
    if not isinstance(data, dict):
        raise TableSemanticLlmError(
            "Semantic table LLM response envelope is invalid."
        )
    return _extract_text_content(data)
