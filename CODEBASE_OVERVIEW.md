# 4.1 Codebase Overview

The project is split into backend and frontend components.

- The `backend` provides authentication, ingestion, retrieval, query answering, agentic workflows, MCP tool exposure, and AstraDB integration.
- The `frontend` provides login, collection management, chat/query UX, file viewing, and document modification workflows.

At a high level, the backend follows this structure:

```text
backend/app/
|-- api/                 # FastAPI routers and HTTP endpoints
|-- service/             # Business logic and workflow orchestration
|-- core/                # Shared dependencies and validation helpers
|-- embedding/           # Embedding clients/providers
|-- vectordb/            # AstraDB vector + parent-document operations
|-- mcp/                 # MCP server, auth, models, and read-only tool services
`-- main.py              # FastAPI application entry point
```

The frontend follows this structure:

```text
frontend/src/
|-- pages/               # Route pages and main workspace UI
|-- components/          # Reusable UI components (e.g., GlobalSidebar)
|-- auth/                # Auth guard, session, and authenticated clients
|-- upload/              # Upload queue state/context
|-- config/              # Frontend environment/config helpers
`-- App.tsx              # Frontend routing entry point
```

# 4.2 Backend API Layer

Directory: `backend/app/api/`

The API layer contains FastAPI routers that expose HTTP endpoints. Routers validate request payloads, enforce auth with dependencies, call service-layer functions, and format responses.

Business logic is primarily delegated to `app/service/` modules.

| Router | Responsibility |
|---|---|
| `router_auth.py` | Auth0 token exchange, internal JWT issuance, user provisioning, auth health check. |
| `router_ingest.py` | Upload ingestion, ingestion strategy routing, async ingest job creation/status. |
| `router_query.py` | Standard RAG query flow, retrieval + answer generation, conversation/message persistence. |
| `router_conversations.py` | Conversation listing, message history retrieval, rename, delete. |
| `router_collections.py` | Collection CRUD, uniqueness checks, default collection handling through service layer. |
| `router_retrieve.py` | File preview listing, parent-chunk pagination, and single-document retrieval for viewer flows. |
| `router_modifications.py` | Manual/LLM-assisted modification endpoints, save jobs, file create/rename/delete, chunk/file updates, streaming selection-preview flow. |
| `router_agent.py` | Agentic query and agentic modification pipelines (standard + skills), sync and streaming endpoints. |

# 4.3 Backend Service Layer

Directory: `backend/app/service/`

The service layer contains backend business logic and orchestration independent of HTTP transport.

```text
backend/app/service/
|-- auth/
|   `-- auth_service.py
|-- collection/
|   `-- collection_service.py
|-- modification/
|   |-- reconstruction_service.py
|   |-- llm_editor_service.py
|   |-- save_job_service.py
|   `-- markdown_chunker.py
|-- rag/
|   |-- ingestion/
|   |-- retrieval/
|   |-- agentic_modification/
|   |-- agentic_modification_skill/
|   `-- agentic_query/
`-- storage/
    `-- s3_image_store.py
```

| Service Module | Responsibility |
|---|---|
| `auth/auth_service.py` | Verifies Auth0 tokens, fetches Auth0 user info, provisions users, issues app JWTs. |
| `collection/collection_service.py` | Collection lifecycle, default collection enforcement, scoping, metadata reconciliation, cascade deletion logic. |
| `modification/reconstruction_service.py` | Reconstructs files from parent chunks, chunk/file update orchestration, file CRUD logic against stores. |
| `modification/llm_editor_service.py` | Generates non-persistent edit previews (full content or selected text) via configured LLM provider. |
| `modification/save_job_service.py` | Background save job queue/status tracking for document-save workflows. |
| `rag/ingestion/` | File parsing (legacy + docling), chunking, canonicalization, upsert orchestration, ingest jobs. |
| `rag/retrieval/` | Retrieval pipeline, reranking, answer generation provider orchestration, query refinement utilities. |
| `rag/agentic_modification/` | Graph-based agentic modification workflow and node orchestration. |
| `rag/agentic_modification_skill/` | Skill-driven agentic modification runtime and tool delegation workflow. |
| `rag/agentic_query/` | Skill-aware agentic query runtime with structured tool-action loop and progressive disclosure. |

# 4.4 Core Utilities

Directory: `backend/app/core/`

The core module provides shared dependencies and reusable utility logic.

| File | Responsibility |
|---|---|
| `dependencies.py` | JWT extraction/verification and current-user/admin dependency injection for FastAPI routes. |
| `db_dependencies.py` | Cached AstraDB collection dependencies (chat messages, conversations, user collections). |
| `validation.py` | Shared validation/sanitization utilities (email/password helpers). |
| `id_utils.py` | UUIDv6 generation helper. |
| `password_utils.py` | Password hashing and verification helpers. |

# 4.5 Embedding Module

Directory: `backend/app/embedding/`

This module provides embedding clients used by vector-store operations.

| File | Responsibility |
|---|---|
| `embedding_client.py` | Beam-based embedding client (`BeamGemmaEmbeddings`) for remote embedding inference. |
| `local_embedding_client.py` | Local embedding client (`LocalGemmaEmbeddings`) for local/dev or fallback embedding generation. |

Provider selection is environment-driven (for example `EMBEDDING_PROVIDER` is resolved during vector DB initialization).

# 4.6 Vector Database Module

Directory: `backend/app/vectordb/`

This module wraps AstraDB operations for parent documents and child vector chunks.

| File | Responsibility |
|---|---|
| `vectordb.py` | Upsert, vector/lexical search, parent-context retrieval, and chunk/file deletion operations. |
| `vectordb_init.py` | Astra collection/index initialization and vector/parent store bootstrapping. |

Other modules interact with Astra through this wrapper instead of duplicating database access logic.

# 4.7 MCP Server Module

Directory: `backend/app/mcp/`

The MCP module exposes selected backend capabilities as authenticated read-only tools.

| Module | Responsibility |
|---|---|
| `server.py` | Creates and configures FastMCP server instance and tool registrations. |
| `auth.py` | JWT token verification for MCP requests and request-scoped user identity extraction. |
| `models.py` | Typed request/response contracts for MCP tool outputs. |
| `service.py` | Read-only collection, search, file, and parent-chunk services used by MCP tools. |

The MCP tools reuse existing backend services (collection, reconstruction, retrieval) to keep behavior consistent with REST flows.

# 4.8 Frontend Module

Directory: `frontend/src/`

The frontend is a React application that handles authenticated user workflows across collections, chat, and document editing/modification.

| Directory / File | Responsibility |
|---|---|
| `pages/login/` | Auth0 login flow and backend token exchange. |
| `pages/collection/` | Collection landing page, collection creation, and scope-to-conversation launch. |
| `pages/conversation/` | Conversation route entry (re-exports main workspace). |
| `pages/mainpage/` | Main workspace UI: chat, documents, uploads, editing, agentic modification UX. |
| `pages/profile/` | Account/profile view and logout flow. |
| `components/GlobalSidebar/` | App-level navigation sidebar. |
| `auth/RequireAuth.tsx` | Route protection and unauthenticated redirect behavior. |
| `auth/apiClient.ts` | Axios/fetch wrappers with auth token injection and 401 handling. |
| `pages/mainpage/hooks/useChat.ts` | Query/chat state, conversation management, standard + agentic query calls. |
| `pages/mainpage/hooks/documents/useDocuments.ts` | File retrieval, file editing state, collection/file actions, and save operations. |
| `pages/mainpage/hooks/documents/subhooks/useDocumentAgent.ts` | AI-assisted document modification proposal workflow. |
| `pages/mainpage/hooks/useResizableLayout.ts` | Resizable desktop layout + responsive behavior state. |
| `upload/` | Upload queue state/context shared across workspace components. |

The frontend communicates with backend HTTP APIs and does not directly access AstraDB, embedding providers, or internal backend services.

# 4.9 API Endpoint Overview

The backend API is organized into router groups:

| API Group | Prefix | Example Purpose |
|---|---|---|
| Authentication API | `/auth` | Auth0 login exchange and internal JWT issuance. |
| Ingestion API | `/ingest` | File upload ingestion and ingest background jobs. |
| Query API | `/api` | Standard RAG query endpoint and answer persistence. |
| Conversations API | `/api` | Conversation list/history/rename/delete operations. |
| Collections API | `/api/collections` | Create, list, rename, and delete user collections. |
| Retrieval API | `/api/retrieve` | File preview lists, parent chunks, and document retrieval for viewer UI. |
| Content Modification API | `/api/modifications` | Manual file/chunk updates, save jobs, LLM preview, file CRUD. |
| Agent API | `/api/agent` | Agentic query and agentic modification (sync + stream). |
| MCP API | `/api/mcp` | Streamable MCP endpoint exposing authenticated read-only retrieval tools. |

This separation keeps boundaries clear between authentication, ingestion, retrieval/query, modification, and agentic tooling.
