# Team44 MCP (Read-Only RAG) README

This module exposes Team44 retrieval capabilities as an MCP server so external agents can query collections and evidence safely.

## What This MCP Server Does

- Transport: Streamable HTTP (`/api/mcp/`).
- Mode: read-only retrieval only (no mutation tools).
- Auth: bearer JWT validated with app `JWT_SECRET_KEY` using `HS256`.
- Scope: every tool is user-scoped from bearer token identity; `user_id` is never a tool input.

The server is created in [server.py](C:/Users/Yoong%20Shen/Desktop/Software%20Engineer%20Group%20Projects/Project%20Codebase/team44_project/backend/app/mcp/server.py), auth is in [auth.py](C:/Users/Yoong%20Shen/Desktop/Software%20Engineer%20Group%20Projects/Project%20Codebase/team44_project/backend/app/mcp/auth.py), business logic is in [service.py](C:/Users/Yoong%20Shen/Desktop/Software%20Engineer%20Group%20Projects/Project%20Codebase/team44_project/backend/app/mcp/service.py), and response contracts are in [models.py](C:/Users/Yoong%20Shen/Desktop/Software%20Engineer%20Group%20Projects/Project%20Codebase/team44_project/backend/app/mcp/models.py).

## How It Is Mounted

FastAPI mounts the MCP ASGI app at `/api/mcp`, and starts MCP session manager in app lifespan:

- Mount point and lifespan integration: [main.py](C:/Users/Yoong%20Shen/Desktop/Software%20Engineer%20Group%20Projects/Project%20Codebase/team44_project/backend/app/main.py)
- Exported symbols: `create_rag_mcp`, `rag_mcp`, `mcp_asgi_app` from [__init__.py](C:/Users/Yoong%20Shen/Desktop/Software%20Engineer%20Group%20Projects/Project%20Codebase/team44_project/backend/app/mcp/__init__.py)

## Authentication Model

`AppJwtTokenVerifier` verifies bearer token claims:

- Required: `sub` (mapped to `user_id` / `client_id`)
- Optional: `email`, `role`, `exp`, `scope`/`scopes`
- Default scope behavior: if `scope/scopes` absent, token gets `rag:read`
- Required MCP scope in server config: `rag:read`

Current user resolution for all tools:

1. Read token from MCP auth context.
2. Resolve `user_id` from `AppAccessToken.user_id` (fallback `client_id`).
3. Reject request if user cannot be resolved.

## Tool Surface

All tools are registered in [server.py](C:/Users/Yoong%20Shen/Desktop/Software%20Engineer%20Group%20Projects/Project%20Codebase/team44_project/backend/app/mcp/server.py) and return structured JSON from Pydantic models.

1. `list_collections()`
Returns user collections and total count.

2. `describe_collection(collectionId?: string, maxFiles: int = 100)`
Returns active collection plus file summaries (`fileId`, `fileName`, `preview`), bounded and truncation-aware.

3. `search_materials(query: string, collectionId?: string, searchScope: "collection" | "all_collections" = "collection", topK: int = 8)`
Runs vector retrieval and returns evidence snippets.

Rules:
- `topK` clamped to `1..20`
- if `searchScope == "all_collections"`, `collectionId` must be omitted
- if `searchScope == "collection"`, results are restricted to file IDs in resolved collection

4. `search_files(query: string, collectionId?: string, limit: int = 10)`
Searches file name/preview text in scoped collection.

Rules:
- `limit` clamped to `1..20`

5. `fetch_parent_chunk(parentId: string, collectionId?: string, maxChars: int = 6000)`
Returns one authorized parent chunk.

Rules:
- `maxChars` clamped to `500..20000`
- parent must belong to current user and to scoped file set

6. `fetch_file_outline(fileId: string, collectionId?: string, maxChunks: int = 40)`
Returns ordered chunk previews for a file.

Rules:
- `maxChunks` clamped to `1..80`
- file must be in scoped collection

## Data/Scope Enforcement

`service.py` enforces scope at two levels:

1. Collection/file scope:
- resolves active collection (`CollectionService.resolve_active_collection`)
- derives allowed file IDs (`CollectionService.list_file_ids_for_collection`)

2. Document ownership scope:
- verifies `metadata.user_id == current_user`
- verifies `metadata.file_metadata.file_id` is in allowed file IDs (when scoped)

Any out-of-scope document is filtered out, even if returned by underlying retrieval.

## Underlying Dependencies Used by MCP Layer

- `CollectionService` for collection resolution and file-id scoping
- `ReconstructionService.get_all_preview_files` for file previews
- `search_and_retrieve_context` for vector retrieval
- `PARENT_STORE` for parent chunk fetch/outline

These are consumed through thin helpers in [service.py](C:/Users/Yoong%20Shen/Desktop/Software%20Engineer%20Group%20Projects/Project%20Codebase/team44_project/backend/app/mcp/service.py).

## Untrusted Text Safety Note

Tool descriptions explicitly state that retrieved document text is untrusted source material and must not be treated as system/developer instructions.

## Environment Variables

Core:

- `JWT_SECRET_KEY` (required for bearer token verification)

Optional MCP auth metadata:

- `MCP_AUTH_ISSUER_URL` (default `http://localhost:8000/auth`)
- `MCP_RESOURCE_SERVER_URL` (default `http://localhost:8000/api/mcp`)

## Local Usage (Agent Client Side)

1. Ensure backend is running and `/api/mcp/` is reachable.
2. Provide bearer token from your app auth flow (JWT with `sub`; scope should include `rag:read` or be omitted for default behavior).
3. Connect MCP client to:

```text
http://127.0.0.1:8000/api/mcp/
```

4. Call `tools/list`, then `tools/call` as needed.

## Testing Coverage

Current MCP-focused tests:

- `backend/tests/test_mcp_auth.py`
- `backend/tests/test_mcp_service.py`
- `backend/tests/test_mcp_server.py`
- `backend/tests/test_mcp_dogfood_absentees.py`

These cover JWT verification behavior, scoping/bounds per tool, tool registration and protocol calls, plus a dogfood flow that answers a meeting absentee question using MCP tools.
