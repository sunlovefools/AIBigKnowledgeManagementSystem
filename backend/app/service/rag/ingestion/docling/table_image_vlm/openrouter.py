# Module purpose:
# Implements image-to-VLM calls via Google Gemini generateContent, including
# request payload building, response parsing, and artifact persistence for
# JSON extraction and summaries.

from __future__ import annotations

import base64
import datetime as dt
import json
import mimetypes
import os
import traceback
from pathlib import Path
from typing import Any

from . import constants
from .artifacts import _write_json_file, _write_text_file
from .prompts import SYSTEM_PROMPT, _build_semantic_summary_prompt


# Keep helper-module style mutable runtime settings on this module for compatibility.
MAX_TOKENS = constants.MAX_TOKENS
MODEL = constants.MODEL
GEMINI_API_BASE_URL = constants.GEMINI_API_BASE_URL
# Legacy alias retained for compatibility with older call sites/tests.
OPENROUTER_URL = GEMINI_API_BASE_URL


def _resolve_api_key(explicit_api_key: str | None = None) -> str:
    """Resolve table-image VLM API key from explicit input or environment."""

    return (
        (explicit_api_key or "").strip()
        or (os.getenv("TABLE_IMAGE_VLM_API_KEY") or "").strip()
        or (os.getenv("GOOGLE_GEMINI_API_KEY") or "").strip()
        or (os.getenv("GEMINI_API_KEY") or "").strip()
        or (os.getenv("TABLE_SEMANTIC_LLM_API_KEY") or "").strip()
        or (os.getenv("OPENROUTER_API_KEY") or "").strip()
    )


def _encode_image_to_data_url(image_path: Path) -> str:
    """Encode a local image file as a `data:<mime>;base64,...` URL."""

    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    mime, _ = mimetypes.guess_type(str(image_path))
    if not mime:
        mime = "application/octet-stream"

    raw = image_path.read_bytes()
    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _encode_image_to_inline_data(image_path: Path) -> tuple[str, str]:
    """Encode image as Gemini inline_data tuple: (mime_type, base64_data)."""

    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    mime, _ = mimetypes.guess_type(str(image_path))
    if not mime:
        mime = "application/octet-stream"

    raw = image_path.read_bytes()
    b64 = base64.b64encode(raw).decode("utf-8")
    return mime, b64


def _extract_json_from_content(content: str) -> Any:
    """Parse JSON from raw model output, handling fenced code blocks and extra text."""

    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _build_generate_content_url() -> str:
    """Build Gemini generateContent endpoint from configured base URL + model."""

    raw_base = (GEMINI_API_BASE_URL or "").strip() or "https://generativelanguage.googleapis.com/v1beta"
    base = raw_base.rstrip("/")
    model = (MODEL or "").strip()
    if not model:
        raise RuntimeError("Table-image VLM model is not configured.")

    if "{model}" in base:
        return base.format(model=model)
    if base.endswith(":generateContent"):
        return base
    if base.endswith("/models"):
        return f"{base}/{model}:generateContent"
    if "/models/" in base:
        if base.endswith(model):
            return f"{base}:generateContent"
        return f"{base}/{model}:generateContent"
    return f"{base}/models/{model}:generateContent"


def _extract_data_url_payload(data_url: str) -> tuple[str, str]:
    """Parse `data:<mime>;base64,<payload>` into (mime, base64_payload)."""

    if not data_url.startswith("data:"):
        raise RuntimeError("Expected data URL image payload for table-image VLM.")
    try:
        header, payload = data_url.split(",", 1)
    except ValueError as exc:
        raise RuntimeError("Malformed data URL image payload.") from exc
    if ";base64" not in header:
        raise RuntimeError("Only base64-encoded data URL images are supported.")
    mime = header[len("data:") :].split(";")[0].strip() or "application/octet-stream"
    return mime, payload


def _call_openrouter_messages(
    *,
    api_key: str,
    messages: list[dict[str, Any]],
    max_tokens: int = MAX_TOKENS,
) -> dict[str, Any]:
    """
    Call Google Gemini generateContent endpoint.

    Function name is retained for backward compatibility with existing imports.
    """

    try:
        import requests
    except ModuleNotFoundError as exc:
        raise RuntimeError("Missing dependency 'requests'. Install it with: pip install requests") from exc

    system_lines: list[str] = []
    user_parts: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        content = message.get("content")
        if role == "system":
            if isinstance(content, str) and content.strip():
                system_lines.append(content.strip())
            continue
        if role != "user":
            continue

        if isinstance(content, str):
            if content.strip():
                user_parts.append({"text": content.strip()})
            continue
        if not isinstance(content, list):
            continue

        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = str(part.get("text") or "").strip()
                if text:
                    user_parts.append({"text": text})
                continue
            if part.get("type") == "image_url":
                image_url = part.get("image_url")
                url = (
                    image_url.get("url")
                    if isinstance(image_url, dict)
                    else ""
                )
                if not isinstance(url, str) or not url.strip():
                    continue
                mime_type, b64_data = _extract_data_url_payload(url.strip())
                user_parts.append(
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_data,
                        }
                    }
                )

    if system_lines:
        user_parts.insert(0, {"text": "\n\n".join(system_lines)})
    if not user_parts:
        raise RuntimeError("No user content provided for Gemini request.")

    token_candidates = [max_tokens, 1000, 512, 256]
    seen: set[int] = set()
    deduped_candidates = [t for t in token_candidates if not (t in seen or seen.add(t))]

    last_status_code: int | None = None
    last_resp_json: dict[str, Any] | None = None
    endpoint = _build_generate_content_url()

    for token_limit in deduped_candidates:
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": user_parts,
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": token_limit,
            },
        }
        resp = requests.post(
            endpoint,
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=500,
        )
        try:
            resp_json = resp.json()
        except Exception as exc:
            raise RuntimeError(
                f"Non-JSON response. HTTP {resp.status_code}. Body:\n{resp.text}"
            ) from exc

        if resp.status_code < 400:
            return resp_json

        last_status_code = resp.status_code
        last_resp_json = resp_json
        # Reduce output tokens for quota/limit style errors.
        if resp.status_code not in {400, 402, 413, 429}:
            break

    raise RuntimeError(f"HTTP {last_status_code}\n{json.dumps(last_resp_json, indent=2)}")


def _get_message_content(resp_json: dict[str, Any]) -> str:
    """Extract text content from a Gemini generateContent response payload."""

    try:
        candidates = resp_json.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise RuntimeError("No candidates in Gemini response.")
        first = candidates[0]
        if not isinstance(first, dict):
            raise RuntimeError("Invalid Gemini candidate payload.")
        content = first.get("content")
        if not isinstance(content, dict):
            raise RuntimeError("Gemini candidate content is missing.")
        parts = content.get("parts")
        if not isinstance(parts, list):
            raise RuntimeError("Gemini candidate parts are missing.")
        texts = [
            str(part.get("text") or "").strip()
            for part in parts
            if isinstance(part, dict) and str(part.get("text") or "").strip()
        ]
        if not texts:
            raise RuntimeError("Gemini response contains no text parts.")
        return "\n".join(texts).strip()
    except Exception as exc:
        raise RuntimeError(f"Unexpected response shape:\n{json.dumps(resp_json, indent=2)}") from exc


def extract_table_json_from_image(
    image_path: Path | str,
    *,
    api_key: str | None = None,
    output_dir: Path | str | None = None,
    save_artifacts: bool = True,
) -> dict[str, Any]:
    """
    Extract a table image into structured JSON using the embedded Gemini helper logic.

    Kept API-compatible with the old `image_processing.openrouter_extract_table` helper.
    """

    image_path_obj = Path(image_path)
    active_output_dir = Path(output_dir) if output_dir is not None else image_path_obj.parent

    out_path = active_output_dir / "output.json"
    raw_text_out_path = active_output_dir / "output_raw.txt"
    # Keep filename for backwards compatibility with existing artifact readers.
    full_response_out_path = active_output_dir / "openrouter_response.json"
    status_out_path = active_output_dir / "status.json"
    error_out_path = active_output_dir / "error.txt"

    resolved_api_key = _resolve_api_key(api_key)
    if not resolved_api_key:
        raise RuntimeError("Gemini API key not found in environment variables.")

    content: str | None = None
    active_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        mime_type, b64_data = _encode_image_to_inline_data(image_path_obj)

        print("Sending image to Google Gemini for JSON extraction...")

        resp_json = _call_openrouter_messages(
            api_key=resolved_api_key,
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract the table from this image and output ONLY valid JSON."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64_data}"},
                        },
                    ],
                },
            ],
        )
        content = _get_message_content(resp_json)
        parsed = _extract_json_from_content(content)

        if save_artifacts:
            _write_json_file(full_response_out_path, resp_json)
            _write_text_file(raw_text_out_path, content)
            _write_json_file(out_path, parsed)
            _write_json_file(
                status_out_path,
                {
                    "ok": True,
                    "timestamp": dt.datetime.now().isoformat(),
                    "source": "google_gemini",
                    "image_path": str(image_path_obj),
                    "output_json": str(out_path),
                    "raw_text": str(raw_text_out_path),
                    "full_response": str(full_response_out_path),
                },
            )

        return parsed
    except Exception as exc:
        if save_artifacts:
            _write_text_file(error_out_path, f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
            if content is not None:
                _write_text_file(raw_text_out_path, content)
            fallback_payload = {
                "ok": False,
                "error": str(exc),
                "error_file": str(error_out_path),
                "raw_text_file": str(raw_text_out_path),
                "timestamp": dt.datetime.now().isoformat(),
            }
            _write_json_file(out_path, fallback_payload)
            _write_json_file(status_out_path, fallback_payload)
        raise


def extract_table_semantic_summary_from_image(
    image_path: Path | str,
    *,
    context_before: str,
    context_after: str,
    api_key: str | None = None,
    output_dir: Path | str | None = None,
    save_artifacts: bool = True,
) -> str:
    """
    Generate a context-aware semantic summary for a table image using Gemini.

    Kept API-compatible with the old `image_processing.openrouter_extract_table` helper.
    """

    image_path_obj = Path(image_path)
    active_output_dir = Path(output_dir) if output_dir is not None else image_path_obj.parent

    summary_out_path = active_output_dir / "semantic_summary.txt"
    raw_text_out_path = active_output_dir / "semantic_raw.txt"
    # Keep filename shorter to reduce Windows path-length pressure for temp writes.
    full_response_out_path = active_output_dir / "semantic_or_response.json"
    status_out_path = active_output_dir / "semantic_status.json"
    error_out_path = active_output_dir / "semantic_error.txt"

    resolved_api_key = _resolve_api_key(api_key)
    if not resolved_api_key:
        raise RuntimeError("Gemini API key not found in environment variables.")

    content: str | None = None
    active_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        mime_type, b64_data = _encode_image_to_inline_data(image_path_obj)
        prompt_text = _build_semantic_summary_prompt(context_before, context_after)

        print("Sending image to Google Gemini for semantic summary extraction...")
        resp_json = _call_openrouter_messages(
            api_key=resolved_api_key,
            max_tokens=800,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64_data}"},
                        },
                    ],
                }
            ],
        )
        content = _get_message_content(resp_json)
        summary_text = " ".join((content or "").strip().split())
        if len(summary_text) > 500:
            summary_text = summary_text[:500].rstrip()

        if save_artifacts:
            _write_json_file(full_response_out_path, resp_json)
            _write_text_file(raw_text_out_path, content)
            _write_text_file(summary_out_path, summary_text)
            _write_json_file(
                status_out_path,
                {
                    "ok": True,
                    "timestamp": dt.datetime.now().isoformat(),
                    "source": "google_gemini",
                    "image_path": str(image_path_obj),
                    "summary_text": str(summary_out_path),
                    "raw_text": str(raw_text_out_path),
                    "full_response": str(full_response_out_path),
                    "context_before_chars": len(context_before),
                    "context_after_chars": len(context_after),
                    "context_before_chars_sent": len((context_before or "")[-2000:]),
                    "context_after_chars_sent": len((context_after or "")[:2000]),
                },
            )

        return summary_text
    except Exception as exc:
        if save_artifacts:
            _write_text_file(error_out_path, f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
            if content is not None:
                _write_text_file(raw_text_out_path, content)
            fallback_payload = {
                "ok": False,
                "error": str(exc),
                "error_file": str(error_out_path),
                "raw_text_file": str(raw_text_out_path),
                "timestamp": dt.datetime.now().isoformat(),
            }
            _write_json_file(status_out_path, fallback_payload)
        raise
