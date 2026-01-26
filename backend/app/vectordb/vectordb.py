from typing import List, Dict, Any, Tuple, Optional
from langchain_core.documents import Document

# Import vector DB initilisation logic
from .vectordb_init import init_vector_db

# --- Global store initialisation ---
RAG_STORES = init_vector_db() # A dictionary with 'vector_store' and 'parent_store' keys which store the LangChain AstraDB objects
VECTOR_STORE = RAG_STORES['vector_store'] # LangChain AstraDBVectorStore for Child Chunks
PARENT_STORE = RAG_STORES['parent_store'] # LangChain AstraDBStore for Parent Documents

# --- Ingestion / Upsertion Operations ---

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

    Notes:
        - The `parent_doc_map` uses the tuple (UUID, Document) format required by LangChain's 
          `PARENT_STORE.mset()` method.
        - The `VECTOR_STORE` handles all network I/O for embedding the child documents.
    """
    
    # 1. Prepare Parent Documents (for key-value storage)
    parent_doc_map: List[Tuple[str, Document]] = []
    for parent_dict in parent_chunks:

        # Ensure required fields are present
        if "_id" not in parent_dict or "content" not in parent_dict:
            raise ValueError("Each parent chunk must have '_id' and 'content' fields.")
        
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

# --- Query/Retrieval Operations ---

async def search_and_retrieve_context(query: str, top_k: int) -> List[str]:
    """
    Performs vector search on child chunks and retrieves the content of their parent documents.

    This implements the "Parent Document Retriever" pattern: it searches small, embedded 
    child chunks and returns the larger, context-rich parent chunks to the LLM.

    Args:
        query (str): The search query (expected to be the refined query).
        top_k (int): The number of top relevant child chunks to search for.

    Returns:
        List[str]: A list of unique string contents from the relevant parent documents.
    """
    print(f"🔍 Searching Vector Store (Child Chunks) for '{query}' (top_k={top_k})...")
    
    # 1. Search the Vector Store (Child Chunks)
    # The LangChain VectorStore handles embedding the query using the configured BeamGemmaEmbeddings.
    try:
        child_documents = await VECTOR_STORE.asimilarity_search_with_score(query, k=top_k)
        print(f"✅ Found {len(child_documents)} relevant child chunks.")
    except Exception as e:
        print(f"❌ Vector Store search failed: {e}")
        raise RuntimeError(f"Vector search failed: {e}")

    if not child_documents:
        return []

    _log_retrieval_debug(query=query, child_docs=child_documents)

    child_documents = [doc for doc, score in child_documents]

    # 2. Extract unique Parent IDs from the retrieved child chunks
    parent_ids = list(
        {doc.metadata["parent_id"] for doc in child_documents if doc.metadata.get("parent_id")}
    )
    print(f"🔗 Retrieving content for {len(parent_ids)} unique parent documents.")
    
    # 3. Retrieve Parent Documents (Full Context)
    try:
        # amget requires a list of keys (parent_ids) and returns a list of Document objects 
        # (which are stored as dictionaries in AstraDBStore)
        parent_documents_dict = await PARENT_STORE.amget(parent_ids)
        
        # The result of amget is a list of Document objects that were serialized to JSON dicts.
        # We need to extract the actual content ('page_content').
        parent_contents = [
            doc["page_content"] for doc in parent_documents_dict
            if doc and "page_content" in doc
        ]
        
        print(f"✅ Retrieved {len(parent_contents)} parent contents as RAG context.")

        _log_retrieval_debug(parent_contents=parent_contents)
        
        return parent_contents

    except Exception as error:
        print(f"❌ Parent Document retrieval failed: {error}")
        raise RuntimeError(f"Parent Document retrieval failed: {error}")
    
def _log_retrieval_debug(
    query: str = "",
    child_docs: Optional[List[Tuple[Document,float]]] = None,
    parent_contents: Optional[List[str]] = None,
    filename: str = "backend/app/service/vectordb/retrieval_debug.txt"
) -> None:
    """
    Helper to append debug information to a text file.
    Handles both Child Chunk logging and Parent Document logging.
    """
    try:
        import os
        os.makedirs(os.path.dirname(filename), exist_ok=True)

        with open(filename, "a", encoding="utf-8") as f:
            
            # Mode 1: Log Child Chunks (Start of retrieval)
            if child_docs is not None:
                f.write(f"\n{'='*50}\n")
                f.write(f"DEBUG: Child chunk with query of: {query}\n")
                f.write(f"{'-'*50}\n")
                
                if not child_docs:
                    f.write("No child chunks found.\n")
                
                for i, (doc, score) in enumerate(child_docs):
                    parent_id = doc.metadata.get("parent_id", "UNKNOWN")
                    f.write(f"[Child Chunk {i+1} | Linked to Parent ID: {parent_id} | Score: {score}]\n")
                    f.write(f"Content: {doc.page_content}\n\n")

            # Mode 2: Log Parent Contents (End of retrieval)
            if parent_contents is not None:
                f.write(f"--- FETCHED PARENT DOCUMENTS ({len(parent_contents)}) ---\n")
                for i, content in enumerate(parent_contents):
                    f.write(f"[Parent Document {i+1}]\n")
                    f.write(f"{content}\n\n")
                f.write(f"{'='*50}\n") # Closing separator

    except Exception as e:
        # Non-blocking error handling: Don't crash the app if logging fails
        print(f"⚠️ Warning: Failed to write to {filename}: {e}")