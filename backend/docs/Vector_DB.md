# Vector Ingestion Pipeline  
**Team44 – Backend Documentation**

This document describes the vector-storage part of the ingestion pipeline.  
It covers how extracted text chunks are embedded using Beam and then written into AstraDB as vector documents.

Last updated: **14 Nov 2025**  
Author: **Aleksandrs Urums**

---

## 1. Purpose

The goal of this module is to store each chunk + metadata + embedding into the AstraDB **document_chunks** collection.

---

## 2. File Overview

### `router_ingest.py`
Main FastAPI router for handling uploaded files.

Responsible for:
- decoding Base64 files,
- extraction via `text_extractor`,
- chunking + polishing,
- embedding request to Beam,
- calling `upsert_chunk` for each processed chunk.

### `embedder.py`
Wraps the Beam Embeddings API.  
Returns `embedding` or `embeddings` depending on Beam response format.

### `vector_store.py`
Final stage: inserts each chunk into AstraDB.

### `vectordb_init.py`
Initialises the AstraDB vector collection (`document_chunks`).  
Ensures schema exists.

---

## 3. Data Flow (End-to-End)

```text
Frontend Upload
   ↓
FastAPI /ingest/webhook
   ↓
extract_text()
   ↓
split_into_chunks()
   ↓
polish_chunks()
   ↓
embed_text()  → Beam Embeddings API
   ↓
upsert_chunk() → AstraDB (document_chunks)
```

---

## 4. Vector Storage Schema

Collection: **document_chunks**

Each stored record has the following structure:

```json
{
  "content": "<cleaned text chunk>",
  "document_name": "Example.pdf",
  "page_number": 0,
  "chunk_number": 0,
  "uploaded_by": "demo-user",
  "timestamp": "2025-11-14T18:19:11.241209",
  "embedding": [ <768 floats> ]
}
```
# 5. Current Working Implementation

### Extraction  
PDF, DOCX, TXT supported.

### Chunking  
Paragraph → sentence → size-based splitting.


### Embeddings  
Beam Embeddings API returns vector dimension 768.

### Vector storage  
Chunks are saved using:

    collection.insert_one(doc)

### Full pipeline tested  
Uploading a PDF through the frontend successfully executes:
    • text extraction  
    • chunking  
    • embedding  
    • storage in AstraDB  

All documents are visible in AstraDB Data Explorer (document_chunks).

---

## 6. TODO / Future Improvements

### 6.1 Correct PDF page numbers  
Currently always saved as 0.  
Extraction module should track page indices.

---

### 6.2 Real user identity  
Currently stored as:

    uploaded_by = "demo-user"

This should be replaced with:
    • actual user ID from JWT  
    • passed through the frontend  

---

### 6.3 Embedding dimension synchronisation  
Beam returns 768 dimensions.  
Initial schema used 1024.  
Team might align:

    • embedding model dimension  
    • vectordb_init dimension  
    • existing data  

---

### 6.4 True UPSERT support  
Right now:

    insert_one(doc)

Future option:

    update_one(
      {"document_name": name, "chunk_number": n},
      {"$set": doc},
      upsert=True
    )

This might be useful if users re-upload documents.

---

### 6.5 Extra metadata to add later

Potential improvements:
    • token count  
    • model version  
    • language detection  
    • file checksum  
    • hierarchical chunk IDs  



## 7. Summary

This module implements a complete ingestion pipeline:

    Document → Text → Chunks → Embeddings → AstraDB

It is fully integrated with the frontend and works end-to-end.  
Future improvements will refine metadata, reliability, and embedding consistency.

---

## 8. Related Files

    backend/app/api/router_ingest.py
    backend/app/service/vector_store.py
    backend/app/service/embedder.py
    backend/app/service/vectordb_init.py
    backend/docs/ingestion.md


