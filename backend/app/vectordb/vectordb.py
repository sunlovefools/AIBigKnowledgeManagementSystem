import asyncio
import inspect
from typing import List, Dict, Any, Tuple
from collections import defaultdict, deque

from langchain_core.documents import Document
from astrapy import DataAPIClient

# Import vector DB initilisation logic
from .vectordb_init import (
    init_vector_db,
    ASTRA_DB_URL,
    ASTRA_DB_TOKEN,
    CHILD_COLLECTION_NAME,
)
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
_RAW_CHILD_COLLECTION = None

# --- Ingestion / Upsertion Operations ---
def _to_deleted_count(delete_result: Any) -> int:
    if isinstance(delete_result, bool):
        return int(delete_result)
    if isinstance(delete_result, (int, float)):
        return int(delete_result)
    if isinstance(delete_result, dict):
        return int(
            delete_result.get("deleted_count")
            or delete_result.get("deletedCount")
            or delete_result.get("n")
            or 0
        )

    deleted_count = getattr(delete_result, "deleted_count", None)
    if deleted_count is not None:
        return int(deleted_count)

    return 0


async def _rollback_parent_documents_by_ids(parent_ids: List[str]) -> int:
    unique_parent_ids = [
        parent_id
        for parent_id in dict.fromkeys(str(raw_parent_id).strip() for raw_parent_id in parent_ids)
        if parent_id
    ]
    if not unique_parent_ids:
        return 0

    def _delete_rows() -> int:
        collection = PARENT_STORE.collection
        filter_doc = {"_id": {"$in": unique_parent_ids}}

        if hasattr(collection, "delete_many"):
            delete_result = collection.delete_many(filter_doc)
            if hasattr(delete_result, "deleted_count"):
                return int(delete_result.deleted_count or 0)

        deleted_count = 0
        for parent_id in unique_parent_ids:
            delete_result = collection.delete_one({"_id": parent_id})
            deleted_count += _to_deleted_count(delete_result)

        return deleted_count

    return await asyncio.to_thread(_delete_rows)


async def _rollback_child_documents_by_ids(child_ids: List[str]) -> int:
    unique_child_ids = [
        child_id
        for child_id in dict.fromkeys(str(raw_child_id).strip() for raw_child_id in child_ids)
        if child_id
    ]
    if not unique_child_ids:
        return 0

    adelete = getattr(VECTOR_STORE, "adelete", None)
    if callable(adelete):
        try:
            delete_result = adelete(ids=unique_child_ids)
        except TypeError:
            delete_result = adelete(unique_child_ids)

        if inspect.isawaitable(delete_result):
            delete_result = await delete_result

        deleted_count = _to_deleted_count(delete_result)
        if deleted_count > 0:
            return deleted_count

        # Some vector stores return bool/None for delete operations.
        return len(unique_child_ids)

    collection = getattr(VECTOR_STORE, "collection", None)
    if collection is None:
        raise RuntimeError("Vector store rollback requires either 'adelete' or a 'collection' attribute.")

    def _delete_rows() -> int:
        filter_doc = {"_id": {"$in": unique_child_ids}}
        if hasattr(collection, "delete_many"):
            delete_result = collection.delete_many(filter_doc)
            if hasattr(delete_result, "deleted_count"):
                return int(delete_result.deleted_count or 0)

        deleted_count = 0
        for child_id in unique_child_ids:
            delete_result = collection.delete_one({"_id": child_id})
            deleted_count += _to_deleted_count(delete_result)

        return deleted_count

    return await asyncio.to_thread(_delete_rows)


async def upsert_documents(parent_chunks: List[Dict[str, Any]], child_chunks: List[Dict[str, Any]]) -> None:
async def upsert_documents(
    parent_chunks: List[Dict[str, Any]],
    child_chunks: List[Dict[str, Any]],
    user_id: str  # ADDED: required so every stored chunk is tagged with its owner
) -> None:
    """
    Inserts Parent (Context) documents and Child (Vector) chunks into the respective AstraDB stores.

    This function orchestrates the persistence phase of the Parent-Child RAG pipeline. It converts
    the input dictionaries (which originated from Pydantic models) into LangChain Document objects,
    ensuring that the Parent-Child relationship (`parent_id`) is maintained.
    """

    # Upsert 1. Prepare Parent Documents (for key-value storage)
    parent_doc_map: List[Tuple[str, Dict[str, Any]]] = []

    # Upsert 2: For each parent chunk, create a LangChain Document and maintain a mapping of parent_id to document dict for upsert.
    print(f"[Upsert Documents] Preparing {len(parent_chunks)} parent documents for upsert...")
    for parent_dict in parent_chunks:
        if "parent_chunk_id" not in parent_dict or "content" not in parent_dict:
            raise ValueError("Each parent chunk must have 'parent_chunk_id' and 'content' fields.")

        parent_metadata = {
            metadata_key: metadata_value
            for metadata_key, metadata_value in parent_dict.items()
            if metadata_key not in ["content", "parent_chunk_id"]
        }

        parent_metadata["user_id"] = user_id  # ADDED: tag parent doc with owner

        parent_doc = Document(
            page_content=parent_dict["content"],
            metadata=parent_metadata,
        )
        json_serializable_doc = parent_doc.dict()
        parent_doc_map.append((parent_dict["parent_chunk_id"], json_serializable_doc))

    # Upsert 3. Prepare Child Documents (for vector storage)
    child_docs: List[Document] = []
    child_doc_ids: List[str] = []

    # Upsert 4: For each child chunk, create a LangChain Document and maintain a list of child documents and their IDs for upsert. 
    # Ensure parent-child relationship is preserved via metadata.
    print(f"[Upsert Documents] Preparing {len(child_chunks)} child documents for upsert...")
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
                "user_id": user_id,  # ADDED: tag child chunk with owner — this is what the search filter matches against
                "file_metadata": child_chunk_dict["file_metadata"],
                "child_chunk_metadata": child_chunk_dict["child_chunk_metadata"],
                "content_flags": content_flags,
                "artifact_refs": artifact_refs,
            },
        )
        child_docs.append(child_doc)
        child_doc_ids.append(child_id)

    print(
        f"[Upsert Documents] Starting concurrent upsert: parents={len(parent_doc_map)} children={len(child_docs)}."
    )

    # Upsert 5: Perform concurrent upsert operations for parents and children, then handle potential failures with rollbacks and retries.
    concurrent_results = await asyncio.gather(
        PARENT_STORE.amset(parent_doc_map),
        VECTOR_STORE.aadd_documents(child_docs, ids=child_doc_ids),
        return_exceptions=True,
    )

    # Upsert 6: Check for exceptions in concurrent upsert results and perform rollbacks if necessary, followed by a sequential retry.
    parent_error = (
        concurrent_results[0]
        if isinstance(concurrent_results[0], Exception)
        else None
    )
    child_error = (
        concurrent_results[1]
        if isinstance(concurrent_results[1], Exception)
        else None
    )

    if parent_error is None and child_error is None:
        print(f"[Upsert Documents] Stored {len(parent_doc_map)} Parent Documents in Document Store.")
        print(f"[Upsert Documents] Stored {len(child_docs)} Child Documents in Vector Store.")
        return

    if parent_error is not None:
        print(f"[Upsert Documents] Concurrent parent upsert failed: {parent_error}")
    if child_error is not None:
        print(f"[Upsert Documents] Concurrent child upsert failed: {child_error}")

    rollback_errors: List[str] = []

    if parent_error is None:
        parent_ids = [parent_id for parent_id, _ in parent_doc_map]
        try:
            deleted_parent_count = await _rollback_parent_documents_by_ids(parent_ids)
            print(
                f"[Upsert Documents] Rolled back concurrently written parent documents: deleted={deleted_parent_count}."
            )
        except Exception as rollback_error:
            rollback_errors.append(f"parent rollback failed: {rollback_error}")
            print(rollback_errors[-1])

    if child_error is None:
        try:
            deleted_child_count = await _rollback_child_documents_by_ids(child_doc_ids)
            print(
                f"[Upsert Documents] Rolled back concurrently written child documents: deleted={deleted_child_count}."
            )
        except Exception as rollback_error:
            rollback_errors.append(f"child rollback failed: {rollback_error}")
            print(rollback_errors[-1])

    print("Retrying upsert sequentially after concurrent failure...")
    try:
        await PARENT_STORE.amset(parent_doc_map)
        print(f"[Upsert Documents] Stored {len(parent_doc_map)} Parent Documents in Document Store (sequential retry).")

        await VECTOR_STORE.aadd_documents(child_docs, ids=child_doc_ids)
        print(f"[Upsert Documents] Stored {len(child_docs)} Child Documents in Vector Store (sequential retry).")
    except Exception as retry_error:
        rollback_context = (
            f" Rollback issues: {'; '.join(rollback_errors)}."
            if rollback_errors
            else ""
        )
        raise RuntimeError(
            "Upsert failed during concurrent write and sequential retry."
            f"{rollback_context} Retry error: {retry_error}"
        ) from retry_error


# --- Deletion Operations (for document updates) ---
async def delete_children_by_parent_id(parent_id: str, user_id: str) -> int:  # ADDED: user_id prevents cross-user deletion
    """
    Deletes all child chunks belonging to a specific parent document.

    Args:
        parent_id: The parent document ID whose children should be removed.
        user_id: The owner of the document — ensures users can only delete their own chunks.

    Returns:
        int: Number of child chunks deleted.
    """
    print(f"🗑️ Deleting child chunks for parent_id={parent_id}...")
    try:
        deleted = await VECTOR_STORE.adelete_by_metadata_filter(
            {
                "child_chunk_metadata.parent_id": parent_id,
                "user_id": user_id,  # ADDED: safety filter — can only delete own chunks
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


async def delete_parent_document(parent_id: str, user_id: str) -> None:  # ADDED: user_id prevents cross-user deletion
    """
    Deletes a single parent document from the Parent Store.

    Args:
        parent_id: The parent document ID to delete.
        user_id: The owner of the document — ensures users can only delete their own documents.
    """
    print(f"🗑️ Deleting parent document {parent_id}...")
    try:
        collection = PARENT_STORE.collection
        # ADDED: user_id added to delete filter — prevents a user from deleting another user's parent doc
        await asyncio.to_thread(
            collection.delete_one,
            {"_id": parent_id, "metadata.user_id": user_id}
        )
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

def _normalize_lexical_child_row(raw_row: Any) -> Dict[str, Any] | None:
    """
    Normalize a raw Astra child-chunk row returned from lexical search.
    """
    if not isinstance(raw_row, dict):
        return None

    lexical_score = (
        raw_row.get("$lexicalScore")
        if "$lexicalScore" in raw_row
        else raw_row.get("lexical_score")
    )

    metadata = raw_row.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    return {
        "_id": raw_row.get("_id"),
        "content": raw_row.get("content"),
        "metadata": metadata,
        "lexical_score": lexical_score,
    }


def _get_raw_child_collection() -> Any:
    """Return a lazy-initialized raw Astra child collection handle."""
    global _RAW_CHILD_COLLECTION
    if _RAW_CHILD_COLLECTION is None:
        if not ASTRA_DB_URL or not ASTRA_DB_TOKEN:
            raise RuntimeError("Astra DB credentials are missing for lexical child-chunk search.")
        client = DataAPIClient()
        _RAW_CHILD_COLLECTION = client.get_database(
            ASTRA_DB_URL, token=ASTRA_DB_TOKEN
        ).get_collection(CHILD_COLLECTION_NAME)
    return _RAW_CHILD_COLLECTION


def _normalize_excluded_file_ids(raw_excluded_file_ids: Any) -> List[str]:
    if isinstance(raw_excluded_file_ids, set):
        candidates = list(raw_excluded_file_ids)
    elif isinstance(raw_excluded_file_ids, list):
        candidates = raw_excluded_file_ids
    elif isinstance(raw_excluded_file_ids, tuple):
        candidates = list(raw_excluded_file_ids)
    else:
        candidates = []

    normalized: List[str] = []
    seen: set[str] = set()
    for item in candidates:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _normalize_included_file_ids(raw_included_file_ids: Any) -> List[str]:
    if isinstance(raw_included_file_ids, set):
        candidates = list(raw_included_file_ids)
    elif isinstance(raw_included_file_ids, list):
        candidates = raw_included_file_ids
    elif isinstance(raw_included_file_ids, tuple):
        candidates = list(raw_included_file_ids)
    else:
        candidates = []

    normalized: List[str] = []
    seen: set[str] = set()
    for item in candidates:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _extract_row_file_id(raw_row: Dict[str, Any]) -> str:
    metadata = raw_row.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    file_metadata = metadata.get("file_metadata")
    if not isinstance(file_metadata, dict):
        file_metadata = {}
    return str(file_metadata.get("file_id") or metadata.get("file_id") or "").strip()


async def lexical_search_child_chunks(
    query: str,
    top_k: int = 20,
    excluded_file_ids: List[str] | set[str] | tuple[str, ...] | None = None,
    included_file_ids: List[str] | set[str] | tuple[str, ...] | None = None,
) -> List[Dict[str, Any]]:
    """
    Perform lexical search directly against the child-chunk Astra collection.
    """
    normalized_query = str(query).strip() if query is not None else ""
    if not normalized_query:
        raise ValueError("query must be a non-empty string.")

    try:
        top_k = int(top_k)
    except (TypeError, ValueError) as error:
        raise ValueError("top_k must be an integer.") from error

    if top_k <= 0:
        raise ValueError("top_k must be greater than 0.")

    collection = _get_raw_child_collection()
    normalized_excluded_file_ids = _normalize_excluded_file_ids(excluded_file_ids)
    normalized_included_file_ids = _normalize_included_file_ids(included_file_ids)

    def _find_rows() -> List[Dict[str, Any]]:
        """
        Internal function to perform lexical search and post-filter excluded file IDs as a safety net.
        """
        if normalized_included_file_ids:
            filter_doc: Dict[str, Any] = {
                "metadata.file_metadata.file_id": {"$in": normalized_included_file_ids}
            }
        elif normalized_excluded_file_ids:
            filter_doc = {"metadata.file_metadata.file_id": {"$nin": normalized_excluded_file_ids}}
        else:
            filter_doc = {}
        try:
            cursor = collection.find(
                filter=filter_doc,
                sort={"$lexical": normalized_query},
                limit=top_k,
            )

            rows: List[Dict[str, Any]] = []
            for row in cursor:
                normalized_row = _normalize_lexical_child_row(row)
                if normalized_row is not None:
                    rows.append(normalized_row)

            if not normalized_excluded_file_ids and not normalized_included_file_ids:
                return rows

            # Enforce exclusion client-side as a safety net.
            filtered_rows: List[Dict[str, Any]] = []
            for row in rows:
                row_file_id = _extract_row_file_id(row)
                if row_file_id in normalized_excluded_file_ids:
                    continue
                if normalized_included_file_ids and row_file_id not in normalized_included_file_ids:
                    continue
                filtered_rows.append(row)

            return filtered_rows[:top_k]
        except Exception as error:
            raise RuntimeError(
                "Lexical child-chunk search failed. Ensure the Astra collection already "
                "supports lexical search and the driver accepts sort={'$lexical': ...}."
            ) from error

    return await asyncio.to_thread(_find_rows)

async def search_and_retrieve_context(query: str, top_k: int) -> List[Dict[str, Any]]:
async def search_and_retrieve_context(
    query: str,
    top_k: int,
    user_id: str  # ADDED: scopes vector search to current user's documents only
) -> List[Dict[str, Any]]:
    """
    Performs vector search on child chunks and retrieves normalized parent document dicts.

    Args:
        query: The search query string.
        top_k: The number of top results to retrieve.
        user_id: The authenticated user's ID — filters results to their documents only.

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
        child_documents_with_scores = await VECTOR_STORE.asimilarity_search_with_score(
            query,
            k=top_k,
            filter={"metadata.user_id": user_id}  # ADDED: only retrieve this user's chunks
        )
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