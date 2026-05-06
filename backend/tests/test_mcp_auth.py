import asyncio
from datetime import datetime, timedelta, timezone

from jose import jwt

from app.mcp.auth import AppJwtTokenVerifier, RAG_READ_SCOPE


def _encode(payload: dict, secret: str = "test-secret") -> str:
    return jwt.encode(payload, secret, algorithm="HS256")


def test_app_jwt_token_verifier_accepts_valid_app_jwt(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    token = _encode(
        {
            "sub": "user-1",
            "email": "user@example.com",
            "role": "user",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
    )

    access_token = asyncio.run(AppJwtTokenVerifier().verify_token(token))

    assert access_token is not None
    assert access_token.user_id == "user-1"
    assert access_token.client_id == "user-1"
    assert access_token.scopes == [RAG_READ_SCOPE]


def test_app_jwt_token_verifier_preserves_explicit_insufficient_scopes(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    token = _encode(
        {
            "sub": "user-1",
            "email": "user@example.com",
            "role": "user",
            "scopes": ["profile"],
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
    )

    access_token = asyncio.run(AppJwtTokenVerifier().verify_token(token))

    assert access_token is not None
    assert access_token.scopes == ["profile"]
    assert RAG_READ_SCOPE not in access_token.scopes


def test_app_jwt_token_verifier_rejects_invalid_and_expired_tokens(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    expired = _encode(
        {
            "sub": "user-1",
            "email": "user@example.com",
            "role": "user",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=5),
        }
    )

    assert asyncio.run(AppJwtTokenVerifier().verify_token("")) is None
    assert asyncio.run(AppJwtTokenVerifier().verify_token("not-a-token")) is None
    assert asyncio.run(AppJwtTokenVerifier().verify_token(expired)) is None


def test_app_jwt_token_verifier_rejects_missing_secret_and_subject(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    unsigned_context_token = "irrelevant"
    assert asyncio.run(AppJwtTokenVerifier().verify_token(unsigned_context_token)) is None

    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    missing_subject = _encode(
        {
            "email": "user@example.com",
            "role": "user",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
    )
    assert asyncio.run(AppJwtTokenVerifier().verify_token(missing_subject)) is None
