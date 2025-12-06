# 📄 QUERY_MODULE.md

### **RAG Query Pipeline – Design & Implementation Overview**

This document describes the complete design of the **Query Module** added to the backend.
It covers the following components:

* `router_query.py`
* `query_refiner.py`
* Updated `vector_store.py` with similarity search
* End-to-end RAG pipeline execution
* API design + request/response schemas
* Limitations and next steps

---

# ✅ 1. Module Overview

The Query Module implements the **Retrieval-Augmented Generation (RAG)** pipeline that powers the system’s document search functionality.
It processes user queries, refines them using an LLM, converts them into embeddings, performs vector similarity search, and returns the top-K most relevant document chunks.

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
(4) Top-K Retrieved Chunks
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
4. Top-K chunk retrieval

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

#### **Step 4 — Format Output**

Each result is converted into a `RetrievedChunk` Pydantic model.

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
 → Build response list
 → QueryResponse returned
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
