# Frontend Register

[Feature index](./README.md) | [Authentication](./authentication.md) | [Ingestion pipeline](./ingestion-pipeline.md) | [Query & RAG](./query-pipeline.md) | [Frontend register](./frontend-register.md) | [Frontend workspace](./frontend-workspace.md)

Feature covering the user registration experience in the React SPA.

## Purpose
- Collect email/password/role with client-side validation.
- Call the backend signup API and route users into the main workspace on success.

## Key code
- Component: `frontend/src/pages/register/Register.tsx`
- Styles: `frontend/src/pages/register/Register.css`
- Routing shell: `frontend/src/App.tsx` defines `/register`.

## Behavior
1) Local state tracks `email`, `password`, `showPassword`, `role`, and `message`.
2) Password regex enforces length plus upper/lower/number/special before sending.
3) Submits `POST {API_BASE}/auth/register` via Axios; success message then `navigate("/mainpage")`.
4) Error handling differentiates backend validation errors vs network failures.
5) Password visibility toggle uses inline SVG icons; role selector offers `user`/`admin`.

## Configuration
- `VITE_API_BASE` in `.env.development`/`.env.production` supplies the backend host.
- Trailing slashes are stripped (`import.meta.env.VITE_API_BASE.replace(/\/$/, "")`).

## Extension ideas
- Add a login page that mirrors this component and stores JWT/refresh tokens.
- Extract Axios client into a shared helper and attach auth headers once backend tokens are available.

## Related backend endpoints
- Depends on `/auth/register`; keep payload `{ email, password, role }` in sync with `router_auth.py`.
