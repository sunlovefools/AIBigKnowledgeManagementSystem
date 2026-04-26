"""Bearer-token authentication for the read-only RAG MCP server."""

from __future__ import annotations

import os
from typing import Any

from jose import JWTError, jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.middleware.auth_context import get_access_token

JWT_ALGORITHM = "HS256"
RAG_READ_SCOPE = "rag:read"


class AppAccessToken(AccessToken):
    """MCP access token enriched with the application user identity."""

    user_id: str
    email: str | None = None
    role: str | None = None


def _normalize_scopes(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [item.strip() for item in raw.split() if item.strip()]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


class AppJwtTokenVerifier(TokenVerifier):
    """Validate existing application JWTs for MCP Streamable HTTP requests."""

    async def verify_token(self, token: str) -> AppAccessToken | None:
        secret = os.getenv("JWT_SECRET_KEY")
        if not secret:
            return None

        try:
            payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
        except JWTError:
            return None

        user_id = str(payload.get("sub") or "").strip()
        if not user_id:
            return None

        email = str(payload.get("email") or "").strip() or None
        role = str(payload.get("role") or "").strip() or None
        expires_at_raw = payload.get("exp")
        expires_at = int(expires_at_raw) if isinstance(expires_at_raw, (int, float)) else None

        explicit_scopes = _normalize_scopes(payload.get("scopes") or payload.get("scope"))
        scopes = explicit_scopes or [RAG_READ_SCOPE]

        return AppAccessToken(
            token=token,
            client_id=user_id,
            scopes=scopes,
            expires_at=expires_at,
            user_id=user_id,
            email=email,
            role=role,
        )


def get_current_user_id() -> str:
    """Return the authenticated application user id for the current MCP request."""

    access_token = get_access_token()
    if isinstance(access_token, AppAccessToken):
        user_id = str(access_token.user_id or "").strip()
    elif access_token is not None:
        user_id = str(access_token.client_id or "").strip()
    else:
        user_id = ""

    if not user_id:
        raise PermissionError("Authentication required.")
    return user_id
