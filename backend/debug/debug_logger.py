from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Specifying the txt file names where debug logs will be stored. These files will be created in backend/debug/logs/
_RETRIEVAL_RERANK_FILE = "retrieval_rerank_debug.txt"
_ANSWER_FILE = "answer_generation_debug.txt"


def _resolve_debug_dir() -> Path:
    """Resolve backend/debug/logs whether running from project root or backend/."""
    cwd = Path.cwd()
    if (cwd / "backend").is_dir():
        backend_dir = cwd / "backend"
    elif cwd.name == "backend":
        backend_dir = cwd
    else:
        backend_dir = Path(__file__).resolve().parent.parent

    debug_dir = backend_dir / "debug" / "logs" # The directory where debug logs will be stored
    debug_dir.mkdir(parents=True, exist_ok=True)
    return debug_dir


def _append(file_name: str, lines: list[str]) -> None:
    """
    Append lines to a debug log file in backend/debug/logs/, creating the file if it doesn't exist.

    Args:
        file_name: The name of the debug log file (e.g., "retrieval_rerank_debug.txt").
        lines: A list of strings to append to the file. Each string will be written on a new line.
    """
    path = _resolve_debug_dir() / file_name # Resolve the full path to the debug log file
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _extract_doc_and_score(item: Any) -> tuple[Any, float | None]:
    """
    Extract doc and score from tuple/object forms without raising.
    
    Args:
        item: The input which can be a document object, a tuple of (doc, score)
              or a document object with score in metadata.

    Returns:        
        A tuple of (doc, score) where doc is the document object and score is a float or None if not found.
    """
    doc = item
    score: float | None = None

    if isinstance(item, tuple):
        if len(item) >= 1:
            doc = item[0]
        if len(item) >= 2:
            try:
                score = float(item[1]) if item[1] is not None else None
            except Exception:
                score = None

    metadata = getattr(doc, "metadata", None)
    if score is None and isinstance(metadata, dict):
        for key in ("bge_score", "score"):
            if key in metadata:
                try:
                    score = float(metadata[key])
                    break
                except Exception:
                    score = None

    return doc, score


def _extract_parent_id(doc: Any) -> str:
    """
    Extract parent ID from a document's metadata if available, otherwise return "N/A".

    Args:
        doc: The document object which contain metadata (In the form of a dictionary) which containing parent ID.
    """
    metadata = getattr(doc, "metadata", None)
    if isinstance(metadata, dict):
        parent_id = metadata.get("parent_id") or metadata.get("parent_doc_id")
        if parent_id is not None:
            return str(parent_id)
    return "N/A"


def _extract_content(doc: Any) -> str:
    """
    Extract content from a document object or dict, handling common cases and avoiding errors. 
    If content cannot be found, returns "N/A".

    Args:
        doc: The document object or dictionary which contain the content in "page_content" field or as an attribute.
    """
    content = getattr(doc, "page_content", None)
    if content is not None:
        return str(content)
    if isinstance(doc, dict):
        content = doc.get("page_content")
        if content is not None:
            return str(content)
    return "N/A"


def log_child_chunks(query: str, child_chunks: list, *, top_k: int | None = None) -> None:
    """
    Append retrieval child chunk debug lines in the expected text format.
    
    Args:
        query: The user query string that was used for retrieval.
        child_chunks: A list of retrieved child chunks, which can be in the form of document objects, tuples of (doc, score), or dicts.
        top_k: Optional integer indicating how many top results were requested for retrieval.
    """
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        lines: list[str] = [
            "=" * 50,
            f"DEBUG: Child chunk with query of: {query}",
            f"Timestamp: {timestamp}",
        ]
        if top_k is not None:
            lines.append(f"Top-K Requested: {top_k}")
        lines.append("-" * 50)

        if not child_chunks:
            lines.append("No child chunks found.")

        for index, item in enumerate(child_chunks, start=1):
            doc, score = _extract_doc_and_score(item)
            parent_id = _extract_parent_id(doc)
            content = _extract_content(doc)
            score_str = f"{score:.6f}" if isinstance(score, float) else "N/A"

            lines.append(f"[Child Chunk {index} | Linked to Parent ID: {parent_id} | Score: {score_str}]")
            lines.append(f"Content: {content}")
            lines.append("")

        lines.append("-" * 50)
        _append(_RETRIEVAL_RERANK_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write retrieval debug log: {exc}")


def log_reranker_results(reranked_docs: list, *, top_k: int = 5) -> None:
    """Append reranker results in the expected text format."""
    try:
        lines: list[str] = [
            "-" * 50,
            f"\u2696\ufe0f  RERANKER RESULTS (Top {top_k} Selected)",
            "-" * 50,
        ]

        if not reranked_docs:
            lines.append("No reranker results.")

        for index, item in enumerate(reranked_docs, start=1):
            content, score = _extract_doc_and_score(item)
            score_str = f"{score:.6f}" if isinstance(score, float) else "N/A"

            lines.append(f"[Rank {index} | BGE Score: {score_str}]")
            lines.append(f"Content: {content}")
            lines.append("")

        lines.append("-" * 50)
        _append(_RETRIEVAL_RERANK_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write reranker debug log: {exc}")


def log_answer_generation_request(provider: str, model: str | None, user_query: str, rag_context: str) -> None:
    """Append answer-generation request block onto backend/debug/logs/answer_generation_request.txt."""
    try:
        timestamp = datetime.now(timezone.utc).isoformat()

        lines = [
            "=" * 50,
            "DEBUG: ANSWER GENERATION REQUEST",
            "-" * 50,
            f"Timestamp: {timestamp}",
            f"Provider: {provider}",
            f"Model: {model if model else 'N/A'}",
            f"Context Length: {len(rag_context)}",
            "",
            "<QUERY>",
            user_query,
            "</QUERY>",
            "",
            "<CONTEXT>",
            rag_context,
            "</CONTEXT>",
            "-" * 50,
        ]
        _append(_ANSWER_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write answer request debug log: {exc}")


def log_answer_generation_response(answer: str) -> None:
    """Append answer-generation response block onto backend/debug/logs/answer_generation_response.txt."""
    try:
        timestamp = datetime.now(timezone.utc).isoformat()

        lines = [
            "-" * 50,
            "DEBUG: ANSWER GENERATION RESPONSE",
            "-" * 50,
            f"Timestamp: {timestamp}",
            f"Answer Length: {len(answer)}",
            "Answer:",
            answer,
            "=" * 50,
        ]
        _append(_ANSWER_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write answer response debug log: {exc}")
