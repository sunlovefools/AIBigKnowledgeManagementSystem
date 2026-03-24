"""Service helpers for ingest upload orchestration and error normalization."""

from __future__ import annotations

import base64
import binascii
from typing import Any

# Internal Imports
from app.core.id_utils import generate_uuid_v6
from app.service.rag.ingestion.legacy.chunk_polisher import polish_chunks
from app.service.rag.ingestion.legacy.chunker import split_parent_child_chunks
from app.service.rag.ingestion.docling import (
    get_pdf_ingestion_strategy,
    parse_pdf_with_docling,
)
from app.service.rag.ingestion.docling.models import DoclingParseResult
from app.service.rag.ingestion.docling.storage import local_artifacts_store
from app.service.rag.ingestion.docling.chunker import (
    split_parent_child_chunks_from_docling_blocks,
)
from app.service.rag.ingestion.docling.pptexcel_extractor import (
    is_pptexcel_document,
    parse_pptexcel_with_docling,
)
from app.service.rag.ingestion.markdown_canonicalizer import (
    canonicalize_chunk_payloads_for_storage,
    canonicalize_markdown_text,
)
from app.service.rag.ingestion.legacy.text_extractor import extract_text
from app.vectordb.vectordb import upsert_documents


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
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidBase64PayloadError("invalid base64 payload") from exc


def is_docling_pdf_strategy(*, content_type: str, pdf_ingestion_strategy: str) -> bool:
    """Return True only when payload is PDF and configured strategy is Docling."""
    return content_type == "application/pdf" and pdf_ingestion_strategy == "docling"


def run_legacy_pipeline(
    *,
    file_name: str,
    content_type: str,
    file_bytes: bytes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Execute legacy (non-Docling) ingestion: extract text, chunk it, and polish child chunks.
    """
    try:
        # Legacy pipeline 1: Extract raw text from uploaded content bytes.
        text = extract_text(content_type, file_bytes)
    except ValueError as exc:
        # Legacy pipeline 1a: Unsupported media type from extractor.
        raise UnsupportedIngestContentTypeError(str(exc)) from exc
    except Exception as exc:
        # Legacy pipeline 1b: Unexpected extraction failure.
        raise LegacyTextExtractionFailedError("text extraction failed") from exc

    # Legacy pipeline 2: Canonicalize markdown/text before chunking for consistency.
    canonical_text = canonicalize_markdown_text(text)

    # Legacy pipeline 3: Split into parent/child chunk models with legacy chunk sizing.
    parent_chunks_models, child_chunks_models = split_parent_child_chunks(
        canonical_text,
        file_name=file_name,
        parent_target_chars=1500,
        child_max_chars=600,
    )

    # Legacy pipeline 4: Convert chunk models into plain dictionaries for downstream storage.
    parent_chunks_dicts = [chunk.model_dump() for chunk in parent_chunks_models]
    child_chunks_dicts = [chunk.model_dump() for chunk in child_chunks_models]

    # Legacy pipeline 5: Polish child chunks to improve vectorization quality.
    polished_child_chunks = polish_chunks(child_chunks_dicts)
    return parent_chunks_dicts, polished_child_chunks

def run_docling_pdf_pipeline(
    *,
    file_name: str,
    file_bytes: bytes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], str]:
    """
    Execute Docling ingestion: parse PDF into structured blocks, then build chunk payloads.
    """
    # Docling-PDF-chunker 1: Generate a file-scoped id used for artifact/chunk linkage.
    file_id = generate_uuid_v6()

    # Docling-PDF-chunker 2: Prepare artifact directory and run_id for this ingestion to be used and returned by the Docling pipeline for logging and artifact storage.
    run_id, artifact_dir, markdown_path = local_artifacts_store.prepare_docling_artifact_dir(
        file_name=file_name,
        artifact_root=None,
    )

    try:
        # Docling-PDF-chunker 3: Parse PDF bytes into structured Docling blocks and metadata.
        # TODO: For the data return from the parse-pdf_docling_fn, a lot of it is not used, may need to refactor and create another lighter model for the return type
        parse_result: DoclingParseResult = parse_pdf_with_docling(
            pdf_bytes=file_bytes,
            file_name=file_name,
            file_id=file_id,
            run_id=run_id,
            artifact_dir=artifact_dir,
            markdown_path=markdown_path,
        )
    except Exception as exc:
        # Docling-PDF-chunker 3a: Normalize parsing failure for router-level HTTP mapping.
        raise DoclingParsingFailedError(str(exc)) from exc

    # Docling-PDF-chunker 4: Ensure parsing produced structured blocks for chunking.
    if not parse_result.structured_blocks:
        raise DoclingNoStructuredBlocksError(
            "docling produced no structured blocks for this PDF"
        )

    try:
        # Docling-PDF-chunker 5: Convert structured blocks into parent/child chunk models.
        parent_chunks_models, child_chunks_models = (
            split_parent_child_chunks_from_docling_blocks(
                blocks=parse_result.structured_blocks,
                file_name=file_name,
                artifact_dir=artifact_dir,
                file_id=file_id,
            )
        )
    except Exception as exc:
        # Docling-PDF-chunker 5a: Normalize chunking failure for router-level HTTP mapping.
        raise DoclingChunkingFailedError(str(exc)) from exc

    # Docling-PDF-chunker 6: Convert chunk models into dictionaries for storage/upsert.
    parent_chunks_dicts = [chunk.model_dump() for chunk in parent_chunks_models]
    child_chunks_dicts = [chunk.model_dump() for chunk in child_chunks_models]

    # Docling-PDF-chunker 7: Aggregate parse warnings and partial-failure summary.
    warnings = list(parse_result.warnings)
    if parse_result.partial_failures:
        warnings.append(
            f"Docling reported {len(parse_result.partial_failures)} partial failure chunk(s)."
        )

    # Docling-PDF-chunker 8: Return chunk payloads, warnings, and Docling run id for logging.
    return parent_chunks_dicts, child_chunks_dicts, warnings, run_id


def run_docling_pptexcel_pipeline(
    *,
    file_name: str,
    content_type: str,
    file_bytes: bytes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], str]:
    """
    Docling branch for PowerPoint (PPTX) and Excel (XLSX) files.
    """

    # Docling-pptexcel 1: Generate a file-scoped id used for artifact/chunk linkage.
    file_id = generate_uuid_v6()

    # docling-pptexcel 2: Prepare artifact directory and run_id for this ingestion to be used and returned by the Docling pipeline for logging and artifact storage.
    run_id, artifact_dir, markdown_path = local_artifacts_store.prepare_docling_artifact_dir(
        file_name=file_name,
        artifact_root=None,
    )

    try:
        # docling-pptexcel 3: Parse PPTX/XLSX file with Docling to get structured blocks
        parse_result = parse_pptexcel_with_docling(
            file_bytes=file_bytes,
            file_name=file_name,
            content_type=content_type,
            file_id=file_id,
            run_id=run_id,
            artifact_dir=artifact_dir,
            markdown_path=markdown_path,
        )
    except Exception as exc:
        raise DoclingParsingFailedError(
            f"Docling PPTX/XLSX parsing failed: {exc}"
        ) from exc

    if not parse_result.structured_blocks:
        raise DoclingNoStructuredBlocksError(
            "Docling produced no structured blocks for this PPTX/XLSX document"
        )

    try:
        # docling-pptexcel 4: Apply structure-aware parent/child chunking (same strategy as PDFs)
        parent_chunks_models, child_chunks_models = (
            split_parent_child_chunks_from_docling_blocks(
                blocks=parse_result.structured_blocks,
                file_name=file_name,
                artifact_dir=artifact_dir,
                file_id=file_id,
            )
        )
    except Exception as exc:
        raise DoclingChunkingFailedError(
            f"Docling PPTX/XLSX chunking failed: {exc}"
        ) from exc

    # docling-pptexcel 5: Convert models to dicts for vector DB
    parent_chunks_dicts = [chunk.model_dump() for chunk in parent_chunks_models]
    child_chunks_dicts = [chunk.model_dump() for chunk in child_chunks_models]
    warnings = list(parse_result.warnings)

    file_type = "PowerPoint" if "presentation" in content_type else "Excel"
    print(
        f"[docling-office] {file_type} chunking complete: "
        f"{len(parent_chunks_dicts)} parents, {len(child_chunks_dicts)} children"
    )

    return (
        parent_chunks_dicts,
        child_chunks_dicts,
        warnings,
        run_id or parse_result.file_id,
    )


async def upsert_chunks(
    *,
    parent_chunks: list[dict[str, Any]],
    child_chunks: list[dict[str, Any]],
) -> None:
    """Upsert parent/child chunks into vector storage and normalize upsert failures."""
    try:
        await upsert_documents(parent_chunks=parent_chunks, child_chunks=child_chunks)
    except Exception as exc:
        raise UpsertChunksFailedError("upsert to vector store failed") from exc


async def run_ingest_upload(
    *,
    file_name: str,
    content_type: str,
    data: str,
) -> dict[str, Any]:
    """
    Unified service-level ingest upload orchestration.
    """
    # Ingestion 1: Decode the file bytes from base64
    file_bytes = decode_base64(data)
    strategy = "legacy"
    warnings: list[str] = []
    run_id: str | None = None

    # Ingestion 2: Route to appropriate ingestion pipeline based on file type
    if is_docling_pdf_strategy(
        content_type=content_type,
        pdf_ingestion_strategy=get_pdf_ingestion_strategy(),
    ):
        # (PDF File type) Docling-pdf strategy
        strategy = "docling-pdf"
        (
            parent_chunks_dicts,
            child_chunks_dicts,
            warnings,
            run_id,
        ) = run_docling_pdf_pipeline(file_name=file_name, file_bytes=file_bytes)

    elif is_pptexcel_document(content_type):
        # (PowerPoint/Excel File type) Docling-pptexcel strategy
        strategy = "docling-pptexcel"
        (
            parent_chunks_dicts,
            child_chunks_dicts,
            warnings,
            run_id,
        ) = run_docling_pptexcel_pipeline(
            file_name=file_name,
            content_type=content_type,
            file_bytes=file_bytes,
        )

    else:
        # (Other File types) Legacy extraction pipeline
        strategy = "legacy"
        parent_chunks_dicts, child_chunks_dicts = run_legacy_pipeline(
            file_name=file_name,
            content_type=content_type,
            file_bytes=file_bytes,
        )

    # Ingestion 3: Canonicalise each of the chunks to ensure consistent format
    parent_chunks_dicts, child_chunks_dicts = canonicalize_chunk_payloads_for_storage(
        parent_chunks_dicts,
        child_chunks_dicts,
    )

    print(
        "[ingest-upload] file=%s strategy=%s parent_chunks=%s child_chunks=%s run_id=%s"
        % (
            file_name,
            strategy,
            len(parent_chunks_dicts),
            len(child_chunks_dicts),
            run_id or "n/a",
        )
    )

    return {
        "parent_chunks": parent_chunks_dicts,
        "child_chunks": child_chunks_dicts,
        "warnings": warnings,
    }
