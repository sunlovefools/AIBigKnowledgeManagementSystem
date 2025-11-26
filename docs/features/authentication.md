# Authentication

[Feature index](./README.md) | [Authentication](./authentication.md) | [Ingestion pipeline](./ingestion-pipeline.md) | [Query & RAG](./query-pipeline.md) | [Frontend register](./frontend-register.md) | [Frontend workspace](./frontend-workspace.md)

Backend feature responsible for registering and logging in users with validation, hashing, and AstraDB persistence.

## Purpose
- Provide `POST /auth/register` and `POST /auth/login` endpoints with consistent responses.
- Enforce email/password rules before creating accounts.
- Store users securely (bcrypt hashes, `is_active` flag) via AstraDB.

## Key code
- Routing: `backend/app/api/router_auth.py`
- Business logic: `backend/app/service/auth_service.py`
- Validation + hashing helpers: `backend/app/core/validation.py`, `backend/app/core/password_utils.py`

## API contracts
- `GET /auth/health` - returns `{"authentication":"ok"}`.
- `POST /auth/register` - body `{ email, password, role }`; returns `{ id, email, created_at, is_active }`; 400 on invalid input, 409 on duplicates.
- `POST /auth/login` - body `{ email, password }`; returns same shape as register; 401 on bad credentials or inactive accounts.

## Flow
1) Router parses request into Pydantic models (`UserCreateRequest`/`UserLoginRequest`).
2) `AuthService` sanitises input, checks AstraDB for existing users, hashes passwords with bcrypt.
3) Successful users are returned through `UserDisplayResponse` (password hash never leaves the server).
4) Errors from `AuthenticationError` are translated into HTTP status codes in the router.

## Configuration
- `.env` in `backend/` must define `ASTRA_DB_URL` and `ASTRA_DB_TOKEN` for the Data API.
- CORS is opened to all origins in `app/main.py` during development; tighten for production.

## Frontend touchpoints
- `frontend/src/pages/register/Register.tsx` posts to `/auth/register` using `VITE_API_BASE` for the host.
- When login UI is added, reuse the same base URL and attach returned profile or token to storage.

## Testing & observability
- Basic auth checks live in `backend/tests/test_latest.py`; extend with tests that hit `/auth/register` and `/auth/login` using `httpx.AsyncClient`.
- Add structured logging around the service for failed attempts when moving to production.
