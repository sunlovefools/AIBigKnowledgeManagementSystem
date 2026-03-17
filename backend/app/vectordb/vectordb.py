import asyncio
from typing import List, Dict, Any, Tuple
from collections import defaultdict, deque

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
_RERANKER_SERVICE = ZeRankerService()

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

        content_flags = child_chunk_dict.get("content_flags") or {
            "is_image": False,
            "is_table_image": False,
        }
        artifact_refs = child_chunk_dict.get("artifact_refs") or {
            "image_uuid": None,
            "table_image_uuid": None,
        }

        child_doc = Document(
            page_content=child_chunk_dict["content"],
            metadata={
                "file_metadata": child_chunk_dict["file_metadata"],
                "child_chunk_metadata": child_chunk_dict["child_chunk_metadata"],
                "content_flags": content_flags,
                "artifact_refs": artifact_refs,
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


async def delete_children_by_file_id(file_id: str) -> int:
    """
    Delete all child chunks that belong to the same logical file.

    This uses `file_metadata.file_id` directly so file deletion does not need to
    iterate over every parent chunk just to clear vector rows.
    """
    print(f"Deleting child chunks for file_id={file_id}...")
    try:
        deleted = await VECTOR_STORE.adelete_by_metadata_filter(
            {
                "file_metadata.file_id": file_id,
            }
        )
        if isinstance(deleted, (int, float)):
            deleted_count = int(deleted)
        elif isinstance(deleted, dict):
            deleted_count = int(
                deleted.get("deleted_count")
                or deleted.get("deletedCount")
                or deleted.get("n")
                or 0
            )
        else:
            deleted_count = int(bool(deleted))
        print(f"  Deleted {deleted_count} child chunks for file_id={file_id}")
        return deleted_count
    except Exception as e:
        print(f"  Failed to delete child chunks for file_id={file_id}: {e}")
        raise RuntimeError(f"Child chunk deletion by file_id failed: {e}")


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


async def delete_parent_documents_by_file_id(file_id: str) -> int:
    """
    Delete every parent document row for a logical file ID.

    We pre-count matching rows first so callers get a stable deleted count even
    if Astra's raw `delete_many` response format varies.
    """

    def _delete_rows() -> int:
        collection = PARENT_STORE.collection
        filter_doc = {"value.metadata.file_metadata.file_id": file_id}
        rows = [row for row in collection.find(filter_doc) if isinstance(row, dict)]
        if not rows:
            return 0
        if hasattr(collection, "delete_many"):
            collection.delete_many(filter_doc)
        else:
            for row in rows:
                parent_id = str(row.get("_id", "")).strip()
                if parent_id:
                    collection.delete_one({"_id": parent_id})
        return len(rows)

    print(f"Deleting parent documents for file_id={file_id}...")
    try:
        deleted_count = await asyncio.to_thread(_delete_rows)
        print(f"  Deleted {deleted_count} parent documents for file_id={file_id}")
        return deleted_count
    except Exception as e:
        print(f"  Failed to delete parent documents for file_id={file_id}: {e}")
        raise RuntimeError(f"Parent document deletion by file_id failed: {e}")


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


def _map_reranked_pairs_to_child_docs(
    child_documents: List[Document],
    reranked_pairs: List[Tuple[str, float]],
) -> List[Tuple[Document, float]]:
    """
    Map reranked (content, score) pairs back to child documents while preserving reranked order.

    Uses a queue per content string so duplicate chunk text is handled deterministically.
    """
    docs_by_content: Dict[str, deque[Document]] = defaultdict(deque)
    for doc in child_documents:
        docs_by_content[doc.page_content].append(doc)

    ordered_docs: List[Tuple[Document, float]] = []
    for content, score in reranked_pairs:
        matches = docs_by_content.get(content)
        if not matches:
            continue
        ordered_docs.append((matches.popleft(), float(score)))

    return ordered_docs


def _select_top_parent_ids_from_reranked_children(
    reranked_child_docs: List[Tuple[Document, float]],
    top_k: int,
) -> List[str]:
    """
    Return unique parent IDs in reranked order, capped at top_k parents.
    """
    parent_ids: List[str] = []
    seen_parent_ids: set[str] = set()

    for doc, _score in reranked_child_docs:
        parent_id = (doc.metadata.get("child_chunk_metadata") or {}).get("parent_id")
        if parent_id is None:
            continue

        parent_id_str = str(parent_id)
        if parent_id_str in seen_parent_ids:
            continue

        seen_parent_ids.add(parent_id_str)
        parent_ids.append(parent_id_str)

        if len(parent_ids) >= top_k:
            break

    return parent_ids


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
    try:
        top_k = int(top_k)
    except (TypeError, ValueError) as error:
        raise ValueError("top_k must be an integer.") from error

    if top_k <= 0:
        raise ValueError("top_k must be greater than 0.")

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

    # Rerank child chunks using the configured reranker model from .env
    reranked_pairs = await _RERANKER_SERVICE.rerank_documents(
        query=query,
        documents=child_texts,
        top_k=top_k // 2,
    )

    reranked_child_docs = _map_reranked_pairs_to_child_docs(
        child_documents=child_documents,
        reranked_pairs=reranked_pairs,
    )

    log_reranker_results(reranked_docs=reranked_pairs[:top_k], top_k=top_k)

    # 2. Extract top-k unique Parent IDs in reranked order.
    parent_ids = _select_top_parent_ids_from_reranked_children(
        reranked_child_docs=reranked_child_docs,
        top_k=top_k,
    )

    if not parent_ids:
        return []

    print(f"Retrieving content for {len(parent_ids)} unique parent documents.")

    # 3. Retrieve Parent Documents (Full Context)
    try:
        parent_documents_raw = await PARENT_STORE.amget(parent_ids)

        parent_documents: List[Dict[str, Any]] = []
        for parent_id, raw_doc in zip(parent_ids, parent_documents_raw):
            if not isinstance(raw_doc, dict):
                continue

            parent_doc_with_id = dict(raw_doc)
            parent_doc_with_id["id"] = parent_id

            normalized = _normalize_parent_document(parent_doc_with_id)
            if normalized is not None:
                parent_documents.append(normalized)

        print(f"Retrieved {len(parent_documents)} parent documents as RAG context.")
        return parent_documents

    except Exception as error:
        print(f"Parent Document retrieval failed: {error}")
        raise RuntimeError(f"Parent Document retrieval failed: {error}")
