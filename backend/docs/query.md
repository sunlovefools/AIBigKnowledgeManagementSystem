# 📄 QUERY_MODULE.md

### **RAG Query Pipeline – Design & Implementation Overview**

This document describes the complete design of the **Query Module** added to the backend.
It covers the following components:

* `router_query.py`
* `query_refiner.py`
* `answer_generator.py` compatibility facade + `answer_generation/` modular package (Ollama/OpenRouter + structured context blocks)
* Updated `vector_store.py` with similarity search
* End-to-end RAG pipeline execution
* API design + request/response schemas
* Limitations and next steps

---

# ✅ 1. Module Overview

The Query Module implements the **Retrieval-Augmented Generation (RAG)** pipeline that powers the system’s document search functionality.
It processes user queries, refines them using an LLM, converts them into embeddings, performs vector similarity search, and generates an answer from retrieved context.

---

# ✅ 2. Architecture Diagram

```
User Query
    ↓
[ Query Router (/api/query) ]
    ↓
(1) Query Refinement (LLM: Qwen via Beam)
    ↓
(2) Embedding Generation
    ↓
(3) Vector Similarity Search (AstraDB)
    ↓
(4) Answer Generation (provider-routed + structured context blocks)
    ↓
Response JSON
```

---

# ✅ 3. router_query.py

`router_query.py` defines two API endpoints:

### **➤ /api/query**

Full RAG pipeline:

1. LLM refinement
2. Embedding generation
3. Vector similarity search
4. Answer generation via compatibility facade -> modular answer_generation providers

### **➤ /api/query/direct**

Embedding + vector search only
(no LLM refinement)

### **Request Model**

```python
class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
```

### **Response Model**

```python
class QueryResponse(BaseModel):
    original_query: str
    refined_query: str
    retrieved_chunks: List[RetrievedChunk]
    chunks_count: int
```

### **Key Logic Summary**

#### **Step 1 — Query Refinement**

```python
refined_query = await refine_query(request.query)
```

#### **Step 2 — Embedding Generation**

```python
embedding_response = await embed_text(session, {"input": [refined_query]})
query_embedding = embedding_response["embedding"][0]
```

#### **Step 3 — Similarity Search**

```python
similar_chunks = search_similar_chunks(query_embedding, top_k=request.top_k)
```

#### **Step 4 — Answer Generation**

The retrieved context is passed to `answer_generator.generate_answer()` (compatibility facade), then delegated to `answer_generation/orchestration.py` which routes to Ollama/OpenRouter provider modules. Both provider paths use a numbered context-block format for answer generation.

---

# ✅ 4. query_refiner.py

This module wraps the **Beam-hosted Qwen LLM service**.

Purpose: convert noisy user queries into better search queries.

### Request Format

```python
payload = {
    "prompt": f"Rewrite the following into a clean search query:\n{query}\nRefined:"
}
```

### Environment Variables

```
BEAM_LLM_URL
BEAM_LLM_KEY
```

### Returned Output

The LLM is expected to return:

```json
{
  "response": "clean refined version"
}
```

---

# ✅ 4A. answer_generator.py + answer_generation package

Answer generation now uses a compatibility facade (`answer_generator.py`) that delegates to the modular `answer_generation/` package for Ollama/OpenRouter provider execution.

### Provider + Env

```
ANSWER_GENERATOR_LLM_PROVIDER=OLLAMA
# Optional for local daemon; required for non-local hosts
OLLAMA_ANSWER_GENERATOR_LLM_URL=http://127.0.0.1:11434/api/generate
OLLAMA_ANSWER_GENERATOR_LLM_MODEL=<required-model-name>
# Optional model fallbacks:
# LOCAL_ANSWER_GENERATOR_LLM_MODEL=<model-name>
# OLLAMA_MODEL=<model-name>
```

Backward-compatible aliases:

```
ANSWER_GENERATOR_LLM_PROVIDER=BEAM    # Alias to OLLAMA code path
BEAM_ANSWER_GENERATOR_LLM_URL
BEAM_ANSWER_GENERATOR_LLM_KEY
```

### Request/Response Contract

- Local target (`localhost` / `127.0.0.1` / `::1`, or URL omitted): uses Python `ollama` client directly (`AsyncClient`)
- Non-local target: request JSON includes `model`, `system`, `prompt`, `stream=false`
- Context is inserted under `<CONTEXT>` as numbered blocks using only `file_name` + `page_content` fields (no `id`, `type`, or full `metadata`)
- No `Authorization` header is sent
- The answer is read from response field `response`
- OpenRouter uses chat-completions `messages` with the same numbered context blocks under `<CONTEXT>` and the same reduced schema

---

# ✅ 5. vector_store.py (Updated)

The ingestion-side `upsert_chunk()` remains unchanged.

### **New Functions Added**

## **5.1 cosine_similarity()**

Computes cosine similarity manually because the legacy AstraDB client does *not* return `$similarity`.

```python
def cosine_similarity(a, b):
    return dot(a,b) / (norm(a) * norm(b))
```

---

## **5.2 search_similar_chunks()**

### Purpose

Perform **vector similarity search** using:

```
sort={"$vector": query_embedding}
```

Because older Data API versions don’t support:

```
collection.vector.find()
```

### Steps

#### ✔ Step 1 — Issue vector search

```python
results = list(collection.find(
    sort={"$vector": query_embedding},
    limit=top_k
))
```

#### ✔ Step 2 — Compute similarity manually

```python
similarity = cosine_similarity(query_embedding, doc["$vector"])
```

#### ✔ Step 3 — Format output

Each chunk returns:

```json
{
  "content": "...",
  "document_name": "...",
  "page_number": 0,
  "chunk_number": 2,
  "similarity_score": 0.87,
  "uploaded_by": "demo-user",
  "timestamp": "2025-11-15T16:21:54.527817"
}
```

---

# ✅ 6. End-to-End Query Pipeline

### Full Pipeline (RAG)

```
User Query
 → refine_query()   
 → embed_text()
 → search_similar_chunks()
 → generate_answer() via Ollama `/api/generate`
 → QueryResponse returned (`answer`)
```

### Direct Mode (no refinement)

```
User Query
 → embed_text()
 → search_similar_chunks()
 → return results
```

Both paths return **top-K relevant document chunks** (default = 5).

---

# ✅ 7. Example Response

```json
{
  "original_query": "What is concurrency in operating systems?",
  "refined_query": "Understanding concurrency in operating systems…",
  "retrieved_chunks": [
    {
      "content": "...",
      "document_name": "COMP2013 Coursework",
      "page_number": 0,
      "chunk_number": 2,
      "similarity_score": 0.82
    }
  ],
  "chunks_count": 5
}
```

---

# ✅ 8. Notes & Limitations

### 1. Older AstraDB Client

Does not support:

```
collection.vector.find()
```

Therefore manual cosine similarity is required.

---

### 2. No LLM hallucination filtering yet

The system returns refined queries directly from Qwen.

---

### 3. No reranking model yet

Only single-pass vector similarity search (Top-K).

---

# ✅ 9. Future Improvements

### (1) Upgrade to AstraDB v2 Vector Search

Enable:

```python
collection.vector.find(... includeSimilarity=True)
```

### (2) Add Context Re-Ranking

Using MiniLM or BGE for better semantic ordering.

### (3) Add multi-document chunk aggregation

So one query can return structured synthesis across multiple documents.

---
