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
        if "_id" not in parent_dict or "content" not in parent_dict:
            raise ValueError("Each parent chunk must have '_id' and 'content' fields.")

        parent_metadata = {
            metadata_key: metadata_value
            for metadata_key, metadata_value in parent_dict.items()
            if metadata_key not in ["content", "_id"]
        }

        parent_doc = Document(
            page_content=parent_dict["content"],
            metadata=parent_metadata,
        )
        json_serializable_doc = parent_doc.dict()
        parent_doc_map.append((parent_dict["_id"], json_serializable_doc))

    # 2. Prepare Child Documents (for vector storage)
    child_docs: List[Document] = []
    for child_chunk_dict in child_chunks:
        child_doc = Document(
            page_content=child_chunk_dict["text"],
            metadata={
                "parent_id": child_chunk_dict["parent_id"],
                "document_name": child_chunk_dict["file_name"],
                "chunk_number": child_chunk_dict["index"],
            },
        )
        child_docs.append(child_doc)

    # 3. Store Parent Documents (Document Store)
    try:
        await PARENT_STORE.amset(parent_doc_map)
        print(f"Stored {len(parent_doc_map)} Parent Documents in Document Store.")
    except Exception as error:
        print(f"Failed to store Parent Documents: {error}")
        raise

    # 4. Store Child Documents (Vector Store - automatically embeds)
    try:
        await VECTOR_STORE.aadd_documents(child_docs)
        print(f"Stored {len(child_docs)} Child Documents in Vector Store.")
    except Exception as error:
        print(f"Failed to store Child Documents: {error}")
        raise


# --- Query/Retrieval Operations ---

async def search_and_retrieve_context(query: str, top_k: int) -> List[str]:
    """
    Performs vector search on child chunks and retrieves the content of their parent documents.

    This implements the "Parent Document Retriever" pattern: it searches small, embedded
    child chunks and returns the larger, context-rich parent chunks to the LLM.
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
        top_k=top_k,
    )

    # Reconstruct the list of Documents based on the reranked order
    reranked_child_docs = []
    for content, _new_score in reranked_pairs:
        for original_doc in child_documents:
            if original_doc.page_content == content:
                reranked_child_docs.append(original_doc)
                break

    log_reranker_results(reranked_docs=reranked_pairs, top_k=top_k)

    # 2. Extract unique Parent IDs from the retrieved child chunks
    parent_ids = list(
        {doc.metadata["parent_id"] for doc in reranked_child_docs if doc.metadata.get("parent_id")}
    )
    print(f"Retrieving content for {len(parent_ids)} unique parent documents.")

    # 3. Retrieve Parent Documents (Full Context)
    try:
        parent_documents_dict = await PARENT_STORE.amget(parent_ids)

        parent_contents = [
            doc["page_content"] for doc in parent_documents_dict if doc and "page_content" in doc
        ]

        print(f"Retrieved {len(parent_contents)} parent contents as RAG context.")
        return parent_contents

    except Exception as error:
        print(f"Parent Document retrieval failed: {error}")
        raise RuntimeError(f"Parent Document retrieval failed: {error}")
