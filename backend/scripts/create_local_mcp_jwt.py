"""Create a local-only long-lived JWT for the read-only MCP server.

This is intended for development machines only. The token is signed with the
backend JWT_SECRET_KEY and carries the rag:read scope required by /api/mcp/.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from jose import jwt


JWT_ALGORITHM = "HS256"
RAG_READ_SCOPE = "rag:read"


def _backend_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _optional_env(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    value = raw.strip()
    if not value or value.startswith("#"):
        return None
    return value.split(" #", 1)[0].strip()


def _load_backend_env() -> None:
    backend_env = _backend_dir() / ".env"
    if backend_env.exists():
        load_dotenv(backend_env)
    else:
        load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a local development JWT for Team44 MCP access.",
    )
    parser.add_argument(
        "--user-id",
        required=True,
        help="Application user id to place in the JWT sub claim.",
    )
    parser.add_argument(
        "--email",
        default="local-mcp@example.test",
        help="Email claim to include in the token.",
    )
    parser.add_argument(
        "--role",
        default="user",
        help="Role claim to include in the token.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Token lifetime in days. Defaults to 365.",
    )
    parser.add_argument(
        "--no-exp",
        action="store_true",
        help="Omit exp entirely. Local development only; not recommended.",
    )
    parser.add_argument(
        "--env-line",
        action="store_true",
        help="Print as MCP_AUTHORIZATION_HEADER='Bearer ...' for local shell/env use.",
    )
    return parser.parse_args()


def main() -> None:
    _load_backend_env()
    args = parse_args()

    secret = _optional_env("JWT_SECRET_KEY")
    if not secret:
        raise SystemExit(
            "JWT_SECRET_KEY is missing. Set it in backend/.env or the current environment."
        )

    user_id = str(args.user_id or "").strip()
    if not user_id:
        raise SystemExit("--user-id must not be empty.")
    if args.days <= 0 and not args.no_exp:
        raise SystemExit("--days must be greater than 0 unless --no-exp is used.")

    payload: dict[str, object] = {
        "sub": user_id,
        "email": str(args.email or "").strip(),
        "role": str(args.role or "user").strip() or "user",
        "scopes": [RAG_READ_SCOPE],
        "token_use": "local_mcp_dev",
        "iat": int(datetime.now(timezone.utc).timestamp()),
    }
    if not args.no_exp:
        expires_at = datetime.now(timezone.utc) + timedelta(days=args.days)
        payload["exp"] = int(expires_at.timestamp())

    token = jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)
    if args.env_line:
        print(f"MCP_AUTHORIZATION_HEADER='Bearer {token}'")
    else:
        print(token)


if __name__ == "__main__":
    main()
