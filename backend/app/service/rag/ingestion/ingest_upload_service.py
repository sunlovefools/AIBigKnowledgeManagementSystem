"""Service helpers for ingest upload orchestration and error normalization."""

from __future__ import annotations

import base64
import binascii
from typing import Any, Awaitable, Callable


class InvalidBase64PayloadError(ValueError):
    pass


class UnsupportedIngestContentTypeError(ValueError):
    pass


class LegacyTextExtractionFailedError(RuntimeError):
    pass


class DoclingParsingFailedError(RuntimeError):
    pass


class DoclingNoStructuredBlocksError(RuntimeError):
    pass


class DoclingChunkingFailedError(RuntimeError):
    pass


class UpsertChunksFailedError(RuntimeError):
    pass


def decode_base64(data: str) -> bytes:
    """Decode a base64 payload and convert malformed input into a domain error."""
    try:
        # Step 1: Validate and decode base64 input bytes.
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        # Step 2: Raise a stable service-layer error for router mapping.
        raise InvalidBase64PayloadError("invalid base64 payload") from exc


def is_docling_pdf_strategy(*, content_type: str, pdf_ingestion_strategy: str) -> bool:
    """Return True only when payload is PDF and configured strategy is Docling."""
    # Step 1: Confirm file is PDF. Step 2: Confirm strategy toggle is "docling".
    return content_type == "application/pdf" and pdf_ingestion_strategy == "docling"


def run_legacy_pipeline(
    *,
    file_name: str,
    content_type: str,
    file_bytes: bytes,
    extract_text_fn: Callable[[str, bytes], str],
    canonicalize_markdown_text_fn: Callable[[str], str],
    split_parent_child_chunks_fn: Callable[..., tuple[list[Any], list[Any]]],
    polish_chunks_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Execute legacy (non-Docling) ingestion: extract text, chunk it, and polish child chunks.
    """
    try:
        # Step 1: Extract raw text from uploaded content bytes.
        text = extract_text_fn(content_type, file_bytes)
    except ValueError as exc:
        # Step 1a: Unsupported media type from extractor.
        raise UnsupportedIngestContentTypeError(str(exc)) from exc
    except Exception as exc:
        # Step 1b: Unexpected extraction failure.
        raise LegacyTextExtractionFailedError("text extraction failed") from exc

    # Step 2: Canonicalize markdown/text before chunking for consistency.
    canonical_text = canonicalize_markdown_text_fn(text)

    # Step 3: Split into parent/child chunk models with legacy chunk sizing.
    parent_chunks_models, child_chunks_models = split_parent_child_chunks_fn(
        canonical_text,
        file_name=file_name,
        parent_target_chars=1500,
        child_max_chars=600,
    )

    # Step 4: Convert chunk models into plain dictionaries for downstream storage.
    parent_chunks_dicts = [chunk.model_dump() for chunk in parent_chunks_models]
    child_chunks_dicts = [chunk.model_dump() for chunk in child_chunks_models]

    # Step 5: Polish child chunks to improve vectorization quality.
    polished_child_chunks = polish_chunks_fn(child_chunks_dicts)
    return parent_chunks_dicts, polished_child_chunks


def run_docling_pipeline(
    *,
    file_name: str,
    file_bytes: bytes,
    generate_uuid_fn: Callable[[], str],
    parse_pdf_with_docling_fn: Callable[..., Any],
    split_parent_child_chunks_from_docling_blocks_fn: Callable[..., tuple[list[Any], list[Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], str]:
    """
    Execute Docling ingestion: parse PDF into structured blocks, then build chunk payloads.
    """
    # Step 1: Generate a file-scoped id used for artifact/chunk linkage.
    file_id = generate_uuid_fn()

    try:
        # Step 2: Parse PDF bytes into structured Docling blocks and metadata.
        parse_result = parse_pdf_with_docling_fn(
            pdf_bytes=file_bytes,
            file_name=file_name,
            file_id=file_id,
        )
    except Exception as exc:
        # Step 2a: Normalize parsing failure for router-level HTTP mapping.
        raise DoclingParsingFailedError(str(exc)) from exc

    # Step 3: Ensure parsing produced structured blocks for chunking.
    if not parse_result.structured_blocks:
        raise DoclingNoStructuredBlocksError(
            "docling produced no structured blocks for this PDF"
        )

    try:
        # Step 4: Convert structured blocks into parent/child chunk models.
        parent_chunks_models, child_chunks_models = (
            split_parent_child_chunks_from_docling_blocks_fn(
                blocks=parse_result.structured_blocks,
                file_name=file_name,
                artifact_dir=parse_result.artifact_dir,
                file_id=file_id,
            )
        )
    except Exception as exc:
        # Step 4a: Normalize chunking failure for router-level HTTP mapping.
        raise DoclingChunkingFailedError(str(exc)) from exc

    # Step 5: Convert chunk models into dictionaries for storage/upsert.
    parent_chunks_dicts = [chunk.model_dump() for chunk in parent_chunks_models]
    child_chunks_dicts = [chunk.model_dump() for chunk in child_chunks_models]

    # Step 6: Aggregate parse warnings and partial-failure summary.
    warnings = list(parse_result.warnings)
    if parse_result.partial_failures:
        warnings.append(
            f"Docling reported {len(parse_result.partial_failures)} partial failure chunk(s)."
        )

    # Step 7: Return chunk payloads, warnings, and Docling run id for logging.
    return parent_chunks_dicts, child_chunks_dicts, warnings, parse_result.artifact_run_id


async def upsert_chunks(
    *,
    parent_chunks: list[dict[str, Any]],
    child_chunks: list[dict[str, Any]],
    upsert_documents_fn: Callable[..., Awaitable[None]],
) -> None:
    """Upsert parent/child chunks into vector storage and normalize upsert failures."""
    try:
        # Step 1: Upsert both chunk sets into the configured vector store.
        await upsert_documents_fn(parent_chunks=parent_chunks, child_chunks=child_chunks)
    except Exception as exc:
        # Step 2: Convert unknown upsert failures into a stable service error.
        raise UpsertChunksFailedError("upsert to vector store failed") from exc
