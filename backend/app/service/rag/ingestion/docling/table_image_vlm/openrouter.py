# Module purpose:
# Implements image-to-VLM calls via OpenRouter, including request payload building,
# response parsing, and artifact persistence for JSON extraction and summaries.

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
OPENROUTER_URL = constants.OPENROUTER_URL


def _encode_image_to_data_url(image_path: Path) -> str:
    """Encode a local image file as a `data:<mime>;base64,...` URL for OpenRouter."""

    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    mime, _ = mimetypes.guess_type(str(image_path))
    if not mime:
        mime = "application/octet-stream"

    raw = image_path.read_bytes()
    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime};base64,{b64}"


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


def _call_openrouter_messages(
    *,
    api_key: str,
    messages: list[dict[str, Any]],
    max_tokens: int = MAX_TOKENS,
) -> dict[str, Any]:
    """Call OpenRouter chat completions with retry-on-402 token fallback behavior."""

    try:
        import requests
    except ModuleNotFoundError as exc:
        raise RuntimeError("Missing dependency 'requests'. Install it with: pip install requests") from exc

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    token_candidates = [max_tokens, 1000, 512, 256]
    seen: set[int] = set()
    deduped_candidates = [t for t in token_candidates if not (t in seen or seen.add(t))]

    last_status_code: int | None = None
    last_resp_json: dict[str, Any] | None = None

    for token_limit in deduped_candidates:
        payload: dict[str, Any] = {
            "model": MODEL,
            "temperature": 0,
            "max_tokens": token_limit,
            "max_completion_tokens": token_limit,
            "provider": {"only": ["alibaba"]},
            "messages": messages,
        }
        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=500)
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

        if resp.status_code != 402:
            break

    raise RuntimeError(f"HTTP {last_status_code}\n{json.dumps(last_resp_json, indent=2)}")


def _get_message_content(resp_json: dict[str, Any]) -> str:
    """Extract the assistant message content from an OpenRouter response payload."""

    try:
        return resp_json["choices"][0]["message"]["content"]
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
    Extract a table image into structured JSON using the embedded OpenRouter helper logic.

    Kept API-compatible with the old `image_processing.openrouter_extract_table` helper.
    """

    image_path_obj = Path(image_path)
    active_output_dir = Path(output_dir) if output_dir is not None else image_path_obj.parent

    out_path = active_output_dir / "output.json"
    raw_text_out_path = active_output_dir / "output_raw.txt"
    full_response_out_path = active_output_dir / "openrouter_response.json"
    status_out_path = active_output_dir / "status.json"
    error_out_path = active_output_dir / "error.txt"

    resolved_api_key = (api_key or os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not resolved_api_key:
        raise RuntimeError("OPENROUTER_API_KEY not found in environment variables.")

    content: str | None = None
    active_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        data_url = _encode_image_to_data_url(image_path_obj)
        
        print("Sending image to OpenRouter for JSON extraction...")

        resp_json = _call_openrouter_messages(
            api_key=resolved_api_key,
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract the table from this image and output ONLY valid JSON."},
                        {"type": "image_url", "image_url": {"url": data_url}},
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
                    "source": "openrouter",
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
    Generate a context-aware semantic summary for a table image using OpenRouter.

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

    resolved_api_key = (api_key or os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not resolved_api_key:
        raise RuntimeError("OPENROUTER_API_KEY not found in environment variables.")

    content: str | None = None
    active_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        data_url = _encode_image_to_data_url(image_path_obj)
        prompt_text = _build_semantic_summary_prompt(context_before, context_after)

        print("Sending image to OpenRouter for semantic summary extraction...")
        resp_json = _call_openrouter_messages(
            api_key=resolved_api_key,
            max_tokens=800,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": data_url}},
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
                    "source": "openrouter",
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
