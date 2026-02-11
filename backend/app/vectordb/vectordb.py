from typing import List, Dict, Any, Tuple, Optional
from langchain_core.documents import Document

# Import vector DB initilisation logic
from .vectordb_init import init_vector_db
from app.service.rag.retrieval.reranker import ZeRankerService

# --- Global store initialisation ---
RAG_STORES = init_vector_db()
VECTOR_STORE = RAG_STORES['vector_store']
PARENT_STORE = RAG_STORES['parent_store']
_RERANKER_SERVICE = ZeRankerService(model_name="BAAI/bge-reranker-v2-m3")


# --- Ingestion / Upsertion Operations ---
async def upsert_documents(parent_chunks: List[Dict[str, Any]], child_chunks: List[Dict[str, Any]]) -> None:

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
            metadata=parent_metadata
        )

        json_serializable_doc = parent_doc.dict()
        parent_doc_map.append((parent_dict["_id"], json_serializable_doc))

    child_docs: List[Document] = []
    for child_chunk_dict in child_chunks:
        child_doc = Document(
            page_content=child_chunk_dict["text"],
            metadata={
                "parent_id": child_chunk_dict["parent_id"],
                "document_name": child_chunk_dict["file_name"],
                "chunk_number": child_chunk_dict["index"],
            }
        )
        child_docs.append(child_doc)

    try:
        await PARENT_STORE.amset(parent_doc_map)
        print(f"✅ Stored {len(parent_doc_map)} Parent Documents in Document Store.")
    except Exception as error:
        print(f"❌ Failed to store Parent Documents: {error}")
        raise

    try:
        await VECTOR_STORE.aadd_documents(child_docs)
        print(f"✅ Stored {len(child_docs)} Child Documents in Vector Store.")
    except Exception as error:
        print(f"❌ Failed to store Child Documents: {error}")
        raise


# --- Query/Retrieval Operations ---
async def search_and_retrieve_context(query: str, top_k: int) -> List[Dict[str, Any]]:

    print(f"🔍 Searching Vector Store (Child Chunks) for '{query}' (top_k={top_k})...")

    try:
        child_documents = await VECTOR_STORE.asimilarity_search_with_score(query, k=top_k * 2)
        print(f"✅ Found {len(child_documents)} relevant child chunks.")
    except Exception as e:
        print(f"❌ Vector Store search failed: {e}")
        raise RuntimeError(f"Vector search failed: {e}")

    if not child_documents:
        return []

    _log_retrieval_debug(query=query, child_docs=child_documents)

    child_documents = [doc for doc, _ in child_documents]
    child_texts = [doc.page_content for doc in child_documents]

    print(f"⚖️  Reranking {len(child_texts)} candidates...")

    reranked_pairs = await _RERANKER_SERVICE.rerank_documents(
        query=query,
        documents=child_texts,
        top_k=top_k
    )

    reranked_child_docs = []
    for content, new_score in reranked_pairs:
        for original_doc in child_documents:
            if original_doc.page_content == content:
                reranked_child_docs.append(original_doc)
                break

    _log_rerank_debug(query=query, reranked_pairs=reranked_pairs)

    parent_ids = list(
        {doc.metadata["parent_id"] for doc in reranked_child_docs if doc.metadata.get("parent_id")}
    )

    print(f"🔗 Retrieving content for {len(parent_ids)} unique parent documents.")

    try:
        parent_documents_dict = await PARENT_STORE.amget(parent_ids)

        structured_results: List[Dict[str, Any]] = []

        for parent_id, doc in zip(parent_ids, parent_documents_dict):
            if not doc:
                continue

            content = doc.get("page_content", "")
            metadata = doc.get("metadata", {})

            # 🔥 FIXED FILENAME EXTRACTION
            filename = (
                metadata.get("document_name")
                or metadata.get("file_name")
                or metadata.get("source")
                or metadata.get("filename")
                or "unknown.pdf"
            )

            structured_results.append({
                "filename": filename,
                "chunk_context": content,
                "page": metadata.get("page", None)
            })

        print(f"✅ Retrieved {len(structured_results)} structured parent contents as RAG context.")

        _log_retrieval_debug(parent_contents=[r["chunk_context"] for r in structured_results])

        return structured_results

    except Exception as error:
        print(f"❌ Parent Document retrieval failed: {error}")
        raise RuntimeError(f"Parent Document retrieval failed: {error}")


# --- Debug Logging ---
def _log_retrieval_debug(
    query: str = "",
    child_docs: Optional[List[Tuple[Document,float]]] = None,
    parent_contents: Optional[List[str]] = None,
    filename: str = "backend/debug/retrieval_debug.txt"
) -> None:
    try:
        import os

        if os.path.basename(os.getcwd()) == "backend" and filename.startswith("backend/"):
            filename = filename.replace("backend/", "", 1)

        os.makedirs(os.path.dirname(filename), exist_ok=True)

        with open(filename, "a", encoding="utf-8") as f:

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

            if parent_contents is not None:
                f.write(f"--- FETCHED PARENT DOCUMENTS ({len(parent_contents)}) ---\n")
                for i, content in enumerate(parent_contents):
                    f.write(f"[Parent Document {i+1}]\n")
                    f.write(f"{content}\n\n")
                f.write(f"{'='*50}\n")

    except Exception as e:
        print(f"⚠️ Warning: Failed to write to {filename}: {e}")


def _log_rerank_debug(
    query: str,
    reranked_pairs: List[Tuple[str, float]],
    filename: str = "backend/debug/retrieval_debug.txt"
) -> None:
    try:
        import os

        if os.path.basename(os.getcwd()) == "backend" and filename.startswith("backend/"):
            filename = filename.replace("backend/", "", 1)

        os.makedirs(os.path.dirname(filename), exist_ok=True)

        with open(filename, "a", encoding="utf-8") as f:
            f.write(f"\n{'-'*50}\n")
            f.write(f"⚖️  RERANKER RESULTS (Top {len(reranked_pairs)} Selected)\n")
            f.write(f"{'-'*50}\n")

            for i, (content, score) in enumerate(reranked_pairs):
                f.write(f"[Rank {i+1} | BGE Score: {score:.4f}]\n")
                f.write(f"Content: {content}\n\n")

            f.write(f"{'-'*50}\n")

    except Exception as e:
        print(f"⚠️ Warning: Failed to write to {filename}: {e}")
