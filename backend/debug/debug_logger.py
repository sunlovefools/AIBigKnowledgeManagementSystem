"""Purpose-built debug logging utilities for RAG retrieval/rerank and answer generation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_RETRIEVAL_RERANK_FILE = "retrieval_rerank_debug.txt"
_ANSWER_FILE = "answer_generation_debug.txt"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated, total_len={len(text)}]"


def _resolve_debug_dir() -> Path:
    """Resolve backend/debug/logs whether running from project root or backend/."""
    cwd = Path.cwd()
    if (cwd / "backend").is_dir():
        backend_dir = cwd / "backend"
    elif cwd.name == "backend":
        backend_dir = cwd
    else:
        backend_dir = Path(__file__).resolve().parent.parent

    debug_dir = backend_dir / "debug" / "logs"
    debug_dir.mkdir(parents=True, exist_ok=True)
    return debug_dir


def _append(file_name: str, lines: list[str]) -> None:
    path = _resolve_debug_dir() / file_name
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _extract_doc_and_score(item: Any) -> tuple[Any, float | None]:
    """Extract doc and score from tuple/object forms without raising."""
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
    metadata = getattr(doc, "metadata", None)
    if isinstance(metadata, dict):
        parent_id = metadata.get("parent_id") or metadata.get("parent_doc_id")
        if parent_id is not None:
            return str(parent_id)
    return "N/A"


def _extract_content(doc: Any) -> str:
    content = getattr(doc, "page_content", None)
    if content is not None:
        return str(content)
    if isinstance(doc, dict):
        value = doc.get("page_content") or doc.get("content") or doc.get("text")
        if value is not None:
            return str(value)
    return "N/A"


def log_child_chunks(query: str, child_chunks: list, *, top_k: int | None = None) -> None:
    """Append retrieval child chunk debug lines in the expected text format."""
    try:
        ts = datetime.now(timezone.utc).isoformat()
        lines: list[str] = [
            "=" * 50,
            f"DEBUG: Child chunk with query of: {query}",
            f"Timestamp: {ts}",
        ]
        if top_k is not None:
            lines.append(f"Top-K Requested: {top_k}")
        lines.append("-" * 50)

        if not child_chunks:
            lines.append("No child chunks found.")

        for idx, item in enumerate(child_chunks, start=1):
            doc, score = _extract_doc_and_score(item)
            parent_id = _extract_parent_id(doc)
            content = _truncate(_extract_content(doc), 320)
            score_str = f"{score:.6f}" if isinstance(score, float) else "N/A"

            lines.append(f"[Child Chunk {idx} | Linked to Parent ID: {parent_id} | Score: {score_str}]")
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

        for idx, item in enumerate(reranked_docs, start=1):
            doc, score = _extract_doc_and_score(item)
            content = _truncate(_extract_content(doc), 320)
            score_str = f"{score:.6f}" if isinstance(score, float) else "N/A"

            lines.append(f"[Rank {idx} | BGE Score: {score_str}]")
            lines.append(f"Content: {content}")
            lines.append("")

        lines.append("-" * 50)
        _append(_RETRIEVAL_RERANK_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write reranker debug log: {exc}")


def log_answer_generation_request(provider: str, model: str | None, user_query: str, rag_context: str) -> None:
    """Append answer-generation request block with safe truncation."""
    try:
        ts = datetime.now(timezone.utc).isoformat()
        safe_context = _truncate(rag_context, 2000)

        lines = [
            "=" * 50,
            "DEBUG: ANSWER GENERATION REQUEST",
            "-" * 50,
            f"Timestamp: {ts}",
            f"Provider: {provider}",
            f"Model: {model if model else 'N/A'}",
            f"Context Length: {len(rag_context)}",
            "",
            "<QUERY>",
            user_query,
            "</QUERY>",
            "",
            "<CONTEXT>",
            safe_context,
            "</CONTEXT>",
            "-" * 50,
        ]
        _append(_ANSWER_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write answer request debug log: {exc}")


def log_answer_generation_response(answer: str) -> None:
    """Append answer-generation response block with safe truncation."""
    try:
        ts = datetime.now(timezone.utc).isoformat()
        safe_answer = _truncate(answer, 2000)

        lines = [
            "-" * 50,
            "DEBUG: ANSWER GENERATION RESPONSE",
            "-" * 50,
            f"Timestamp: {ts}",
            f"Answer Length: {len(answer)}",
            "Answer:",
            safe_answer,
            "=" * 50,
        ]
        _append(_ANSWER_FILE, lines)
    except Exception as exc:
        print(f"Warning: failed to write answer response debug log: {exc}")
