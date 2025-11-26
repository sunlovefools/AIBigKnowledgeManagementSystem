# Workspace Page (MainPage)

**Navigation:** [Pages overview](../README.md) | [Register page](../register/README.md) | [Frontend doc](../../../FRONTEND_README.md) | [Ingestion API](../../../../backend/app/api/README.md) | [Query/RAG API](../../../../backend/app/api/README.md)

This folder contains the chat-style workspace for asking questions and uploading documents.

## Files
- `MainPage.tsx`
  - **Non-technical:** The chat/workspace screen where users talk to the AI and upload documents.
  - **Technical:** Manages chat state, file uploads, and Axios calls to backend query and ingestion endpoints.
- `MainPage.css` — layout and styling for chat bubbles, inputs, and header.

## Behavior highlights
- Tracks messages, text input, selected file, and backend connectivity response.
- `handleSend()` posts `{ query }` to `POST {VITE_API_BASE}/api/query` then appends AI replies; if a file is selected, posts `{ fileName, contentType, data }` to `POST {VITE_API_BASE}/ingest/webhook`.
- Auto-scrolls to newest messages and renders AI responses with Markdown highlighting.
- `handleBackendTestClick()` pings `GET {VITE_API_BASE}/hello` to verify connectivity.
- `handleLogout()` clears local storage and routes to `/register`.
  - **Non-technical:** Feels like a chat app: type, get an answer, and see your uploaded file noted in the thread.
  - **Technical:** Uses React hooks/refs for state and scroll control; uses `react-markdown` + `remark-gfm` + `rehype-highlight` for rendering AI output.

## Related systems
- Ingestion and query routers are documented in `backend/app/api/README.md` and implemented in `backend/app/service/`.
- Routing shell is defined in `src/App.tsx`.

Update this README if endpoint paths, payloads, or state management change.
