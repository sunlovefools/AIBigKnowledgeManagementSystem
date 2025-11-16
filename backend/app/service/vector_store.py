from typing import List, Optional
from datetime import datetime

from .vectordb_init import init_vector_db

# Initialize the vector database and get the collection object (If the collection does not exist, it will be created, if it exists, the existing one will be used)
collection = init_vector_db()


def upsert_chunk(
    content: str,
    document_name: str,
    page_number: int,
    chunk_number: int,
    uploaded_by: str,
    embedding: List[float],
    timestamp: Optional[str] = None,
) -> None:
    
    #Stores one chunk in AstraDB (the document_chunks collection).
    #For now, i'll just do a simple Insert_one.

    if timestamp is None:
        timestamp = datetime.utcnow().isoformat()

    doc = {
        "content": content,
        "document_name": document_name,
        "page_number": page_number,
        "chunk_number": chunk_number,
        "uploaded_by": uploaded_by,
        "timestamp": timestamp,
        "$vector": embedding,
    }

    collection.insert_one(doc)

import numpy as np

def cosine_similarity(a, b):
    a = np.array(a, dtype=float)
    b = np.array(b, dtype=float)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def search_similar_chunks(query_embedding, top_k=5):
    """
    Vector similarity search for old AstraDB Data API client.
    Uses sort={"$vector": query_embedding}.
    Manually computes similarity because old API does not return $similarity.
    """

    # 1) Perform vector search using the old API
    results = list(collection.find(
        sort={"$vector": query_embedding},
        limit=top_k
    ))

    formatted = []

    for doc in results:

        # 2) Compute cosine similarity manually
        similarity = None
        if "$vector" in doc:
            try:
                similarity = cosine_similarity(query_embedding, doc["$vector"])
            except:
                similarity = None

        # 3) Build response item
        formatted.append({
            "content": doc.get("content"),
            "document_name": doc.get("document_name"),
            "page_number": doc.get("page_number"),
            "chunk_number": doc.get("chunk_number"),
            "uploaded_by": doc.get("uploaded_by"),
            "timestamp": doc.get("timestamp"),
            "similarity_score": similarity,
        })

    return formatted