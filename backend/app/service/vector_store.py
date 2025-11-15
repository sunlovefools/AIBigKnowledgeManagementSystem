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
