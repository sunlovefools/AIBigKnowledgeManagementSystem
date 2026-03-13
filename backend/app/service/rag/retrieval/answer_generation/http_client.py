"""Shared async HTTP helper for provider API calls.

This module centralizes JSON POST behavior and error normalization across providers.
It exists to avoid duplicated timeout/status handling code. It should not know about
provider-specific payload semantics.
"""

from __future__ import annotations

from typing import Any

import aiohttp


async def post_json(
    session: aiohttp.ClientSession,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_s: float,
    error_prefix: str,
) -> dict[str, Any]:
    """POST JSON and return parsed JSON with normalized RuntimeError handling.

    Args:
        session: Active aiohttp session.
        url: Target endpoint URL.
        payload: JSON body payload.
        headers: HTTP request headers.
        timeout_s: Total request timeout in seconds.
        error_prefix: Prefix included in normalized error messages.

    Returns:
        Parsed JSON response object.

    Raises:
        RuntimeError: On timeout, client errors, status errors, or non-object JSON.
    """
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
