# Frontend Documentation (React + TypeScript + Vite)

This document describes the complete web client located in `frontend/`. It covers architecture, project layout, major UI flows, environment configuration, build steps, and integration points with the FastAPI backend/RAG services.

---

## Overview

- **Purpose**: Provide the user-facing portal for uploading knowledge documents, registering accounts, and chatting with the AI assistant.
- **Framework**: React 19 with React Router DOM 7 rendered via Vite 7. TypeScript enforces type safety.
- **State Management**: Component-level `useState` hooks; no external store yet.
- **HTTP Client**: Axios 1.13 handles auth, chat queries, and ingestion uploads.
- **Styling**: Plain CSS modules per page (`MainPage.css`, `Register.css`) plus global resets (`App.css`, `index.css`).

The app currently delivers two screens (`/register`, `/mainpage`) with navigation defined in `App.tsx`. Additional pages (login/dashboard) can follow the same folder pattern under `src/pages`.

---

## Project Layout

```
frontend/
├── public/                    # Static assets copied into the final build
├── src/
│   ├── App.tsx                # Router shell, nav links, route definitions
│   ├── main.tsx               # React 19 entry + StrictMode
│   ├── App.css / index.css    # Global styles + layout utilities
│   ├── pages/
│   │   ├── register/
│   │   │   ├── Register.tsx   # Registration form + validation
│   │   │   └── Register.css
│   │   └── mainpage/
│   │       ├── MainPage.tsx   # Chat workspace with upload + query flow
│   │       └── MainPage.css
│   ├── assets/                # Placeholder images/fonts
│   └── vite-env.d.ts          # Vite/TS type augmentation
├── package.json / package-lock.json
├── tsconfig*.json             # `app`, `node`, `base`
├── eslint.config.js
├── vite.config.ts
├── .env.development           # VITE_API_BASE for dev
└── .env.production            # VITE_API_BASE for prod
```

---

## Environment & Configuration

The frontend reads backend URLs from Vite env vars:

```
frontend/.env.development
VITE_API_BASE=http://127.0.0.1:8000

frontend/.env.production
VITE_API_BASE=https://<deployed-backend-host>
```

In code:

```ts
const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");
```

Always strip trailing slashes to avoid double `//` when constructing endpoint URLs.

---

## Key Screens

### 1. **Register Page** (`src/pages/register/Register.tsx`)

- Local state: `email`, `password`, `showPassword`, `role`, `message`.
- Validates passwords with regex enforcing upper/lower/number/special (>=8 chars).
- Submits to `POST {API_BASE}/auth/register` with `{ email, password, role }`.
- On success: displays a banner, clears the form, and `navigate("/mainpage")`.
- Handles Axios errors explicitly (API responses vs. network).
- UI features:
  - Password visibility toggle (SVG icons).
  - Role dropdown (`user`, `admin`).
  - Inline success/error message area.

### 2. **Main Workspace Page** (`src/pages/mainpage/MainPage.tsx`)

Responsibilities:

- Maintains conversational state (`messages`) rendered with avatars and file chips.
- Uses `useEffect` + `listRef` for auto-scroll when new messages arrive.
- Allows text entry + file uploads (PDF/DOC/Image). Files convert to base64 via `FileReader` and are stored in `fileContent`.
- `handleSend()` workflow:
  1. Pushes the user message into local chat state.
  2. Calls `POST {API_BASE}/api/query` with `{ query: <text> }`.
  3. Appends AI response or error placeholder.
  4. If a file was selected, posts to `POST {API_BASE}/ingest/webhook` with `{ fileName, contentType, data: <base64> }`.
  5. Clears the input + file picker.
- Additional actions:
  - `handleLogout()` clears `localStorage.token` and redirects to `/register`.
  - Hidden “Backend test” button triggers `GET {API_BASE}/hello` and stores the result in `response`.
  - Shows welcome placeholder UI when no messages exist yet.

State summary:

| Hook            | Purpose                                               |
|-----------------|-------------------------------------------------------|
| `messages`      | Array of `{ role: "user" | "ai", text, fileName? }`.  |
| `input`         | Textarea content.                                     |
| `selectedFile`  | `File` object chosen via hidden `<input type="file">`.|
| `fileContent`   | Base64 string for ingestion API.                      |
| `response`      | Output from `/hello` diagnostics (dev only).          |
| `fileRef`       | Ref to hidden file input, triggered by upload button. |
| `listRef`       | Ref to scrollable chat container.                     |

---

## Development Workflow

```bash
cd frontend
npm install
npm run dev            # Starts Vite dev server on http://localhost:5173
```

Ensure the backend server is running and `VITE_API_BASE` matches its URL. React Router handles navigation; use the top nav to switch between register/main pages during development.

### Linting

```bash
npm run lint
```

Currently uses ESLint 9 with configs in `eslint.config.js`. Type-aware rules require the `tsconfig.node.json` + `tsconfig.app.json` references defined there.

### Production Build

```bash
npm run build      # Generates dist/
npm run preview    # Serves the optimized build locally
```

Artifacts in `dist/` can be deployed to GitHub Pages or any static host. For GitHub Pages, ensure the base path/environment variables are set via CI to point to the deployed backend (see root README deployment section).

---

## Integration Points

| Purpose            | Endpoint (relative to `VITE_API_BASE`) | Notes |
|--------------------|----------------------------------------|-------|
| Health check       | `GET /hello`                           | Surfaced through hidden test button on `MainPage`. |
| Register user      | `POST /auth/register`                  | Expect `{ detail }` error message on failure. |
| Query LLM          | `POST /api/query` (or `/query`)        | Replace path once backend finalizes route; currently used for placeholder responses. |
| Upload document    | `POST /ingest/webhook`                 | Accepts base64 payload + metadata for ingestion pipeline. |

When authentication is implemented, attach `Authorization: Bearer <token>` headers to both query and ingestion requests. Consider centralizing axios configuration in a helper module once login is added.

---

## Future Enhancements

1. **Login & Session Handling**  
   - Add `/auth/login` UI.  
   - Store JWT/refresh tokens in `localStorage` and configure axios interceptors.

2. **Error UX**  
   - Replace inline strings with toast notifications.  
   - Indicate loading states for long-running uploads or queries.

3. **Component Tests**  
   - Introduce Vitest + React Testing Library to cover Register/MainPage logic.

4. **Design System**  
   - Migrate CSS files to a consistent component library or CSS-in-JS solution.  
   - Replace placeholder glyphs with production-ready icons.

5. **State Management**  
   - Evaluate Zustand/Redux if multi-page state (auth, chat history) grows complex.

6. **Accessibility**  
   - Add focus handling for chat input, ARIA labels for file attachments, and keyboard shortcuts for sending messages.

---

## Quick Reference

| Command          | Description                          |
|------------------|--------------------------------------|
| `npm run dev`    | Start dev server (HMR).              |
| `npm run lint`   | ESLint check.                        |
| `npm run build`  | Production bundle to `dist/`.        |
| `npm run preview`| Serve the production bundle locally. |

Use this README when onboarding new frontend contributors or syncing with backend teammates on API expectations. For deployment specifics (GitHub Pages, CI secrets), refer to the root `README.md`.
