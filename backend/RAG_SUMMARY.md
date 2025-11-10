## Deliverable Summary – RAG Orchestration and Vector Store Layer

### 1. Included Files

| File                           | Purpose                                                                                                                                               |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/service/orchestration.py` | Implements the Retrieval-Augmented Generation (RAG) pipeline. Connects the FastAPI backend with the LLM/Embedding models and AstraDB vector database. |
| `app/service/query_service.py` | Provides the interface between FastAPI routes and the RAG orchestrator. Handles query processing and user document queries.                           |
| `app/service/vector_store.py`  | Manages the connection to AstraDB and performs vector similarity searches. Includes user document statistics and debugging utilities.                 |

---

### 2. Functional Overview

1. The **frontend** sends a POST request to `/query`.
2. The **FastAPI route** calls `query_service.process_query()`.
3. The **query service** uses `get_orchestrator()` to access the RAG pipeline.
4. The **RAG orchestrator** executes the following steps:

   * `refine_query()` → calls `query_llm()` (LLM refinement)
   * `retrieve_relevant_chunks()` → calls `get_embedding()` and `similarity_search()` in `vector_store`
   * `format_context()` → combines retrieved chunks
   * `generate_response()` → calls `query_llm()` again for final answer
5. The result is returned to the frontend as JSON with `answer`, `sources`, and `refined_query`.

---

### 3. Functional and Test Status

| Component                 | Status           | Notes                                           |
| ------------------------- | ---------------- | ----------------------------------------------- |
| LLM and Embedding (Beam)  | Mock mode active | Real endpoints not yet deployed                 |
| AstraDB Connection        | Working          | Verified with `debug_list_collections()`        |
| RAG Pipeline Execution    | Working          | End-to-end tested in mock mode                  |
| FastAPI Route Integration | Compatible       | `/query` endpoint functional                    |
| Document Ingestion        | Pending          | `document_vectors` collection not yet populated |

---

### 4. Environment Variables

| Variable         | Description                           |
| ---------------- | ------------------------------------- |
| `ASTRA_DB_URL`   | AstraDB instance URL                  |
| `ASTRA_DB_TOKEN` | AstraDB access token                  |
| `LLM_URL`        | Optional Beam inference endpoint      |
| `EMBED_URL`      | Optional Beam embedding endpoint      |
| `USE_MOCK_BEAM`  | Set to `true` for mock testing        |
| `BEAM_TIMEOUT`   | Optional request timeout (default 60) |

---

### 5. Testing

* **Integration Test:** `tests/test_orchestration_run.py` runs the entire RAG pipeline.
* **Database Check:** `VectorStoreService.debug_list_collections()` lists collections to confirm database access.

Example test output (mock mode):

```
[MOCK] Simulating LLM response...
[MOCK] Generating fake embedding vector...
Collections in DB: ['users']
RAG Pipeline Result:
Answer: [MOCK LLM] Response to: ...
Sources Used: 0
```

---

### 6. Next Steps

| Step | Description                                           | Responsible             |
| ---- | ----------------------------------------------------- | ----------------------- |
| 1    | Populate the `document_vectors` collection in AstraDB | Document Ingestion team |
| 2    | Deploy real Beam-hosted LLM and embedding models      | Model team              |
| 3    | Disable mock mode and run live RAG tests              | Backend team            |
| 4    | Connect the `/query` endpoint to the frontend         | Frontend team           |

---

### 7. Summary

This delivery includes a functional and tested RAG orchestration layer for the backend.
It can process user queries end-to-end in mock mode and is ready for integration with AstraDB and Beam once those components are online.

---