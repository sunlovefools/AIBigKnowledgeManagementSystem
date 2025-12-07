from typing import List, Dict, Any, Tuple
from langchain_core.documents import Document

# Import the initialization function which runs once at module load
from .vectordb_init import init_vector_db

# Initialize stores once on module load
# These variables hold the ready-to-use, globally accessible LangChain AstraDB objects.
RAG_STORES = init_vector_db()
VECTOR_STORE = RAG_STORES['vector_store'] # LangChain AstraDBVectorStore for Child Chunks
PARENT_STORE = RAG_STORES['parent_store'] # LangChain AstraDBStore for Parent Documents


# --- INGESTION/UPSERTION OPERATIONS ---

async def upsert_documents(parent_chunks: List[Dict[str, Any]], child_chunks: List[Dict[str, Any]]) -> None:
    """
    Inserts Parent (Context) documents and Child (Vector) chunks into the respective AstraDB stores.

    This function orchestrates the persistence phase of the Parent-Child RAG pipeline. It converts 
    the input dictionaries (which originated from Pydantic models) into LangChain Document objects, 
    ensuring that the Parent-Child relationship (`parent_id`) is maintained. Crucially, calling 
    `VECTOR_STORE.add_documents()` triggers the automatic, asynchronous embedding of the Child 
    Chunks using the configured Beam Embeddings service.

    Args:
        parent_chunks (List[Dict[str, Any]]): List of large parent document dictionaries. Must 
                                              contain the AstraDB primary key '_id' (from the 
                                              Pydantic alias) and 'content'.
        child_chunks (List[Dict[str, Any]]): List of polished child chunk dictionaries. Must 
                                             contain the text ('text') and the foreign key 
                                             ('parent_id') linking back to the parent.

    Returns:
        None: The function handles persistence internally and does not return data.

    Example:
        >>> # Assume parent_list and child_list are valid List[Dict] objects
        >>> upsert_documents(parent_list, child_list)
        ✅ Stored X Parent Documents in Document Store.
        ✅ Stored Y Child Documents in Vector Store.

    Notes:
        - The `parent_doc_map` uses the tuple (UUID, Document) format required by LangChain's 
          `PARENT_STORE.mset()` method.
        - The `VECTOR_STORE` handles all network I/O for embedding the child documents.
    """
    
    # 1. Prepare Parent Documents (for key-value storage)
    parent_doc_map: List[Tuple[str, Document]] = []
    for parent_dict in parent_chunks:

        # Extract metadata keys, excluding 'content' and the primary key '_id'
        parent_metadata = {
            metadata_key: metadata_value 
            for metadata_key, metadata_value in parent_dict.items() 
            if metadata_key not in ["content", "_id"]
        }
        
        parent_doc = Document(
            page_content=parent_dict["content"],
            metadata=parent_metadata
        )
        # Convert to JSON-serializable dict for AstraDBStore
        json_serializable_doc = parent_doc.dict()
        # Store as a tuple (key, Document object)
        parent_doc_map.append((parent_dict["_id"], json_serializable_doc))
        
    # 2. Prepare Child Documents (for vector storage)
    child_docs: List[Document] = []
    for child_chunk_dict in child_chunks:
        # Construct the Document object from the polished child dictionary
        child_doc = Document(
            page_content=child_chunk_dict["text"],
            metadata={
                "parent_id": child_chunk_dict["parent_id"],
                "document_name": child_chunk_dict["file_name"], 
                "chunk_number": child_chunk_dict["index"], 
            }
        )
        child_docs.append(child_doc)

    # 3. Store Parent Documents (Document Store)
    try:
        await PARENT_STORE.amset(parent_doc_map)
        print(f"✅ Stored {len(parent_doc_map)} Parent Documents in Document Store.")
    except Exception as error:
        print(f"❌ Failed to store Parent Documents: {error}")
        raise

    # 4. Store Child Documents (Vector Store - automatically embeds)
    try:
        await VECTOR_STORE.aadd_documents(child_docs)
        print(f"✅ Stored {len(child_docs)} Child Documents in Vector Store.")
    except Exception as error:
        print(f"❌ Failed to store Child Documents: {error}")
        raise