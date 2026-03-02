"""LLM editor service for natural-language driven document edit preview."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any
from uuid import uuid4

import aiohttp

try:
    from backend.debug.debug_logger import log_token_usage
except ImportError:
    from debug.debug_logger import log_token_usage

_OPENROUTER_DEFAULT_URL = "https://openrouter.ai/api/v1/chat/completions"
_DEFAULT_TIMEOUT_S = 500.0

_SYSTEM_PROMPT = (
    "You are a document editor assistant. "
    "Apply user instruction to the provided document content. "
    "Return ONLY valid JSON with keys: edited_content (string), summary (string), warnings (array of strings). "
    "Do not add markdown code fences."
)


@dataclass(frozen=True)
class _LlmEditorConfig:
    provider: str
    timeout_s: float
    beam_url: str | None
    beam_key: str | None
    openrouter_url: str
    openrouter_api_key: str | None
    openrouter_model: str


def _normalize_chat_completions_url(raw_url: str) -> str:
    """Normalize provider URL to a Chat Completions endpoint."""
    url = (raw_url or "").strip()
    if not url:
        return _OPENROUTER_DEFAULT_URL

    normalized = url.rstrip("/")
    lowered = normalized.lower()

    if lowered.endswith("/chat/completions"):
        return normalized
    if lowered.endswith("/v1"):
        return f"{normalized}/chat/completions"
    if lowered.endswith("/api"):
        return f"{normalized}/v1/chat/completions"

    return f"{normalized}/chat/completions"


def _load_config() -> _LlmEditorConfig:
    provider = os.getenv("LLM_EDITOR_PROVIDER", "OPENROUTER").strip().upper()
    timeout_raw = (os.getenv("LLM_EDITOR_TIMEOUT_S") or str(_DEFAULT_TIMEOUT_S)).strip()

    try:
        timeout_s = float(timeout_raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid LLM_EDITOR_TIMEOUT_S value {timeout_raw!r}. Expected a number.") from exc

    if timeout_s <= 0:
        raise RuntimeError("LLM_EDITOR_TIMEOUT_S must be > 0.")

    beam_url = os.getenv("LLM_EDITOR_BEAM_URL") or os.getenv("BEAM_LLM_URL")
    beam_key = os.getenv("LLM_EDITOR_BEAM_KEY") or os.getenv("BEAM_LLM_KEY")

    openrouter_url_raw = (os.getenv("OPENROUTER_URL") or "").strip() or _OPENROUTER_DEFAULT_URL
    openrouter_url = _normalize_chat_completions_url(openrouter_url_raw)
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
    openrouter_model = os.getenv("LLM_EDITOR_OPENROUTER_MODEL") or os.getenv("OPENROUTER_MODEL") or "deepseek/deepseek-r1:free"
    openrouter_model = openrouter_model.strip()

    return _LlmEditorConfig(
        provider=provider,
        timeout_s=timeout_s,
        beam_url=beam_url,
        beam_key=beam_key,
        openrouter_url=openrouter_url,
        openrouter_api_key=openrouter_api_key,
        openrouter_model=openrouter_model,
    )


def _build_user_prompt(file_name: str, instruction: str, original_content: str) -> str:
    payload = {
        "file_name": file_name,
        "instruction": instruction,
        "original_content": original_content,
        "required_output_schema": {
            "edited_content": "string",
            "summary": "string",
            "warnings": ["string"],
        },
        "constraints": [
            "Keep language and structure unless instruction requests otherwise.",
            "If no change is needed, return original_content as edited_content.",
            "Output must be valid JSON only.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


async def _post_json(
    *,
    session: aiohttp.ClientSession,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_s: float,
    error_prefix: str,
) -> dict[str, Any]:
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with session.post(url, json=payload, headers=headers, timeout=timeout) as response:
            if response.status != 200:
                response_text = await response.text()
                raise RuntimeError(f"{error_prefix} ({response.status}): {response_text}")

            data = await response.json()
            if not isinstance(data, dict):
                raise RuntimeError(f"{error_prefix}: expected JSON object response.")
            return data
    except TimeoutError as exc:
        raise RuntimeError(f"{error_prefix}: request timed out after {timeout_s} seconds.") from exc
    except aiohttp.ClientError as exc:
        raise RuntimeError(f"{error_prefix}: HTTP client error: {exc}") from exc


def _extract_json_from_text(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None

    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    fenced_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", stripped)
    if fenced_match:
        try:
            parsed = json.loads(fenced_match.group(1))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

    object_match = re.search(r"(\{[\s\S]*\})", stripped)
    if object_match:
        try:
            parsed = json.loads(object_match.group(1))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    return None


def _normalize_preview_result(candidate: dict[str, Any] | None, original_content: str) -> dict[str, Any]:
    result = candidate or {}

    edited_content = result.get("edited_content")
    if not isinstance(edited_content, str):
        edited_content = result.get("editedContent")
    if not isinstance(edited_content, str):
        edited_content = result.get("content")
    if not isinstance(edited_content, str):
        edited_content = original_content

    summary = result.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        if edited_content.strip() == original_content.strip():
            summary = "No meaningful edits were generated."
        else:
            summary = "LLM edit preview generated successfully."

    warnings_raw = result.get("warnings")
    if isinstance(warnings_raw, list):
        warnings = [str(item) for item in warnings_raw if str(item).strip()]
    else:
        warnings = []

    return {
        "editedContent": edited_content,
        "summary": summary.strip(),
        "warnings": warnings,
    }


def _log_preview_token_usage(
    *,
    data: dict[str, Any],
    provider: str,
    model: str,
    run_id: str,
) -> None:
    usage = data.get("usage") if isinstance(data, dict) else {}
    if not isinstance(usage, dict):
        usage = {}

    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)

    log_token_usage(
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=0.0,
        operation="modification_llm_edit_preview",
        run_id=run_id,
        step="llm_edit_preview",
    )


async def _generate_via_openrouter(
    *,
    session: aiohttp.ClientSession,
    cfg: _LlmEditorConfig,
    file_name: str,
    original_content: str,
    instruction: str,
    run_id: str,
) -> dict[str, Any]:
    if not cfg.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required when LLM_EDITOR_PROVIDER=OPENROUTER.")

    payload = {
        "model": cfg.openrouter_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_user_prompt(file_name, instruction, original_content),
            },
        ],
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {cfg.openrouter_api_key}",
        "Content-Type": "application/json",
    }

    data = await _post_json(
        session=session,
        url=cfg.openrouter_url,
        payload=payload,
        headers=headers,
        timeout_s=cfg.timeout_s,
        error_prefix="LLM editor OpenRouter API error",
    )

    _log_preview_token_usage(
        data=data,
        provider="OPENROUTER",
        model=cfg.openrouter_model,
        run_id=run_id,
    )

    text_content = ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        message = first_choice.get("message") if isinstance(first_choice, dict) else {}
        if isinstance(message, dict):
            maybe_content = message.get("content")
            if isinstance(maybe_content, str):
                text_content = maybe_content

    parsed = _extract_json_from_text(text_content)
    return _normalize_preview_result(parsed, original_content)


async def _generate_via_beam(
    *,
    session: aiohttp.ClientSession,
    cfg: _LlmEditorConfig,
    file_name: str,
    original_content: str,
    instruction: str,
    run_id: str,
) -> dict[str, Any]:
    if not cfg.beam_url or not cfg.beam_key:
        raise RuntimeError(
            "BEAM provider configuration missing. Set LLM_EDITOR_BEAM_URL/LLM_EDITOR_BEAM_KEY "
            "or fallback BEAM_LLM_URL/BEAM_LLM_KEY."
        )

    prompt = _build_user_prompt(file_name, instruction, original_content)
    payload = {
        "file_name": file_name,
        "instruction": instruction,
        "original_content": original_content,
        "prompt": prompt,
        "user_query": prompt,
    }
    headers = {
        "Authorization": f"Bearer {cfg.beam_key}",
        "Content-Type": "application/json",
    }

    data = await _post_json(
        session=session,
        url=cfg.beam_url,
        payload=payload,
        headers=headers,
        timeout_s=cfg.timeout_s,
        error_prefix="LLM editor BEAM API error",
    )

    _log_preview_token_usage(
        data=data,
        provider="BEAM",
        model="llm-editor-beam",
        run_id=run_id,
    )

    if isinstance(data.get("edited_content"), str) or isinstance(data.get("editedContent"), str):
        return _normalize_preview_result(data, original_content)

    text_candidate = data.get("answer") if isinstance(data.get("answer"), str) else ""
    parsed = _extract_json_from_text(text_candidate)
    return _normalize_preview_result(parsed, original_content)


class LlmEditorService:
    """Service responsible for generating edit previews from natural-language instructions."""

    @staticmethod
    async def generate_edit_preview(
        *,
        file_name: str,
        original_content: str,
        instruction: str,
    ) -> dict:
        cfg = _load_config()
        run_id = uuid4().hex

        async with aiohttp.ClientSession() as session:
            if cfg.provider == "OPENROUTER":
                return await _generate_via_openrouter(
                    session=session,
                    cfg=cfg,
                    file_name=file_name,
                    original_content=original_content,
                    instruction=instruction,
                    run_id=run_id,
                )

            if cfg.provider == "BEAM":
                return await _generate_via_beam(
                    session=session,
                    cfg=cfg,
                    file_name=file_name,
                    original_content=original_content,
                    instruction=instruction,
                    run_id=run_id,
                )

            raise RuntimeError(
                f"Invalid LLM_EDITOR_PROVIDER: {cfg.provider}. Expected 'OPENROUTER' or 'BEAM'."
            )
