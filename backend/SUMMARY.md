# Backend Authentication Summary

## Current Auth Model

- Backend authentication is **Auth0-only**.
- Supported auth API endpoints:
  - `GET /auth/health`
  - `POST /auth/auth0-login`
- Legacy local-password endpoints (`/auth/register`, `/auth/login`) are removed.

## Auth Service Responsibilities

- `auth0_login(token)`:
  - Validates Auth0 access token (JWKS, audience, issuer).
  - Resolves `sub` and `email` claims (falls back to Auth0 `/userinfo` if needed).
  - Delegates to OAuth user login/provisioning and returns session payload.

- `oauth_login(email, oauth_sub)`:
  - Logs in existing OAuth user by `oauth_sub`.
  - Auto-creates new users with:
    - `auth_provider="oauth"`
    - `user_role="user"`
  - Issues backend JWT for subsequent protected API calls.

- `get_user_by_email(email)`:
  - Returns basic user profile if present.

## Migration

- One-time script to remove legacy local users:
  - `backend/scripts/migrate_auth0_only_remove_local_users.py`
- Target rows:
  - users where `auth_provider == "local"`
- Script supports dry run:
  - `python backend/scripts/migrate_auth0_only_remove_local_users.py --dry-run`

## Tests

- `backend/tests/test_latest.py` verifies:
  - local auth methods/routes are absent
  - Auth0 auth methods/routes are present
