import asyncio
from typing import List, Dict, Any, Tuple

from langchain_core.documents import Document

# Import vector DB initilisation logic
from .vectordb_init import init_vector_db
from app.service.rag.retrieval.reranker import ZeRankerService

try:
    from backend.debug.debug_logger import log_child_chunks, log_reranker_results
except ImportError:
    from debug.debug_logger import log_child_chunks, log_reranker_results

# --- Global store initialisation ---
RAG_STORES = init_vector_db()  # A dictionary with 'vector_store' and 'parent_store' keys
VECTOR_STORE = RAG_STORES['vector_store']  # LangChain AstraDBVectorStore for Child Chunks
PARENT_STORE = RAG_STORES['parent_store']  # LangChain AstraDBStore for Parent Documents
_RERANKER_SERVICE = ZeRankerService(model_name="BAAI/bge-reranker-v2-m3")

# --- Ingestion / Upsertion Operations ---
async def upsert_documents(parent_chunks: List[Dict[str, Any]], child_chunks: List[Dict[str, Any]]) -> None:
    """
    Inserts Parent (Context) documents and Child (Vector) chunks into the respective AstraDB stores.

    This function orchestrates the persistence phase of the Parent-Child RAG pipeline. It converts
    the input dictionaries (which originated from Pydantic models) into LangChain Document objects,
    ensuring that the Parent-Child relationship (`parent_id`) is maintained.
    """

    # 1. Prepare Parent Documents (for key-value storage)
    parent_doc_map: List[Tuple[str, Document]] = []

    for parent_dict in parent_chunks:
        if "parent_chunk_id" not in parent_dict or "content" not in parent_dict:
            raise ValueError("Each parent chunk must have 'parent_chunk_id' and 'content' fields.")

        parent_metadata = {
            metadata_key: metadata_value
            for metadata_key, metadata_value in parent_dict.items()
            if metadata_key not in ["content", "parent_chunk_id"]
        }


        parent_doc = Document(
            page_content=parent_dict["content"],
            metadata=parent_metadata,
        )
        json_serializable_doc = parent_doc.dict()
        parent_doc_map.append((parent_dict["parent_chunk_id"], json_serializable_doc))

    # 2. Prepare Child Documents (for vector storage)
    child_docs: List[Document] = []
    child_doc_ids: List[str] = []
    for child_chunk_dict in child_chunks:
        child_id = child_chunk_dict.get("child_chunk_id")
        if not child_id:
            raise ValueError("Each child chunk must have 'child_chunk_id'.")

        child_doc = Document(
            page_content=child_chunk_dict["content"],
            metadata={
                "file_metadata": child_chunk_dict["file_metadata"],
                "child_chunk_metadata": child_chunk_dict["child_chunk_metadata"],
            },
        )
        child_docs.append(child_doc)
        child_doc_ids.append(child_id)

    try:
        await PARENT_STORE.amset(parent_doc_map)
        print(f"Stored {len(parent_doc_map)} Parent Documents in Document Store.")
    except Exception as error:
        print(f"Failed to store Parent Documents: {error}")
        raise

    try:
        await VECTOR_STORE.aadd_documents(child_docs, ids=child_doc_ids)
        print(f"Stored {len(child_docs)} Child Documents in Vector Store.")
    except Exception as error:
        print(f"Failed to store Child Documents: {error}")
        raise


# --- Deletion Operations (for document updates) ---
async def delete_children_by_parent_id(parent_id: str) -> int:
    """
    Deletes all child chunks belonging to a specific parent document.

    Args:
        parent_id: The parent document ID whose children should be removed.

    Returns:
        int: Number of child chunks deleted.
    """
    print(f"🗑️ Deleting child chunks for parent_id={parent_id}...")
    try:
        deleted = await VECTOR_STORE.adelete_by_metadata_filter(
            {
                "child_chunk_metadata.parent_id": parent_id,
            }
        )
        print(f"  ✅ Deleted child chunks for parent_id={parent_id}")
        return deleted
    except Exception as e:
        print(f"  ❌ Failed to delete child chunks: {e}")
        raise RuntimeError(f"Child chunk deletion failed: {e}")


async def delete_parent_document(parent_id: str) -> None:
    """
    Deletes a single parent document from the Parent Store.

    Args:
        parent_id: The parent document ID to delete.
    """
    print(f"🗑️ Deleting parent document {parent_id}...")
    try:
        collection = PARENT_STORE.collection
        await asyncio.to_thread(collection.delete_one, {"_id": parent_id})
        print(f"  ✅ Deleted parent document {parent_id}")
    except Exception as e:
        print(f"  ❌ Failed to delete parent document: {e}")
        raise RuntimeError(f"Parent document deletion failed: {e}")


def _normalize_parent_document(raw_doc: Any) -> Dict[str, Any] | None:
    """
    Normalize a stored parent doc into a JSON-serializable dict shape.
    
    Args:
        raw_doc: The raw document object retrieved from the parent store, 
        which is in the form of LangChain Document
    """
    if not isinstance(raw_doc, dict):
        return None

    page_content = raw_doc.get("page_content")
    if page_content is None:
        return None

    metadata = raw_doc.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    return {
        "id": raw_doc.get("id"),
        "metadata": metadata,
        "page_content": str(page_content),
        "type": str(raw_doc.get("type", "Document")),
    }


# --- Query/Retrieval Operations ---

async def search_and_retrieve_context(query: str, top_k: int) -> List[Dict[str, Any]]:
    """
    Performs vector search on child chunks and retrieves normalized parent document dicts.

    Args:
        query: The search query string.
        top_k: The number of top results to retrieve.

    Returns:
        List[Dict[str, Any]]: JSON-serializable parent documents with keys:
            id, metadata, page_content, type
    """
    print(f"Searching Vector Store (Child Chunks) for {query!r} (top_k={top_k})...")

    # 1. Search the Vector Store (Child Chunks)
    try:
        child_documents_with_scores = await VECTOR_STORE.asimilarity_search_with_score(query, k=top_k * 2)
        print(f"Found {len(child_documents_with_scores)} relevant child chunks.")
    except Exception as error:
        print(f"Vector Store search failed: {error}")
        raise RuntimeError(f"Vector search failed: {error}")

    if not child_documents_with_scores:
        return []

    log_child_chunks(query=query, child_chunks=child_documents_with_scores, top_k=top_k)

    child_documents = [doc for doc, _ in child_documents_with_scores]
    child_texts = [doc.page_content for doc in child_documents]

    print(f"Reranking {len(child_texts)} candidates...")

    # Rerank the retrieved child chunks using the BGE Reranker
    reranked_pairs = await _RERANKER_SERVICE.rerank_documents(
        query=query,
        documents=child_texts,
        top_k=top_k * 2,
    )

    reranked_child_docs = []
    for content, _new_score in reranked_pairs:
        for original_doc in child_documents:
            if original_doc.page_content == content:
                reranked_child_docs.append(original_doc)
                break

    log_reranker_results(reranked_docs=reranked_pairs, top_k=top_k)

    # 2. Extract unique Parent IDs from the retrieved child chunks
    parent_ids = list(
        {
            (doc.metadata.get("child_chunk_metadata") or {}).get("parent_id")
            for doc in reranked_child_docs
            if (doc.metadata.get("child_chunk_metadata") or {}).get("parent_id")
        }
    )
    print(f"Retrieving content for {len(parent_ids)} unique parent documents.")

    # 3. Retrieve Parent Documents (Full Context)
    try:
        parent_documents_raw = await PARENT_STORE.amget(parent_ids)

        for index in range(len(parent_ids)):
            parent_documents_raw[index]["id"] = parent_ids[index]

        parent_documents: List[Dict[str, Any]] = []
        for raw_doc in parent_documents_raw:
            normalized = _normalize_parent_document(raw_doc)
            if normalized is not None:
                parent_documents.append(normalized)

        print(f"Retrieved {len(parent_documents)} parent documents as RAG context.")
        return parent_documents

    except Exception as error:
        print(f"Parent Document retrieval failed: {error}")
        raise RuntimeError(f"Parent Document retrieval failed: {error}")
