"""Service helpers for ingest upload orchestration and error normalization."""

from __future__ import annotations

import asyncio
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
from app.service.rag.ingestion.docling.table_semantic import (
    TableSemanticIngestionError,
    process_semantic_tables_for_pdf,
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


def _sort_semantic_parents(parent_chunks_models: list[Any]) -> list[Any]:
    """Sort semantic parents deterministically by source table block and group index."""

    def _key(model: Any) -> tuple[int, int, str]:
        metadata = getattr(model, "parent_chunk_metadata", {}) or {}
        semantic = metadata.get("table_semantic") if isinstance(metadata, dict) else {}
        if not isinstance(semantic, dict):
            semantic = {}
        table_block_index = semantic.get("table_block_index", 10**9)
        group_index = semantic.get("group_index", 10**9)
        try:
            table_block_index = int(table_block_index)
        except (TypeError, ValueError):
            table_block_index = 10**9
        try:
            group_index = int(group_index)
        except (TypeError, ValueError):
            group_index = 10**9
        return table_block_index, group_index, str(getattr(model, "parent_chunk_id", ""))

    return sorted(parent_chunks_models, key=_key)


def _sort_semantic_children(child_chunks_models: list[Any]) -> list[Any]:
    """Sort semantic children deterministically by source table block and slice index."""

    def _key(model: Any) -> tuple[int, int, str]:
        metadata = getattr(model, "child_chunk_metadata", {}) or {}
        slice_meta = metadata.get("table_slice") if isinstance(metadata, dict) else {}
        if not isinstance(slice_meta, dict):
            slice_meta = {}
        table_block_index = slice_meta.get("table_block_index", 10**9)
        slice_index = slice_meta.get("slice_index", 10**9)
        try:
            table_block_index = int(table_block_index)
        except (TypeError, ValueError):
            table_block_index = 10**9
        try:
            slice_index = int(slice_index)
        except (TypeError, ValueError):
            slice_index = 10**9
        return table_block_index, slice_index, str(getattr(model, "child_chunk_id", ""))

    return sorted(child_chunks_models, key=_key)


def _parent_source_order_key(model: Any) -> tuple[int, int, int, str]:
    """Return document-source ordering for standard and semantic parent chunks."""

    metadata = getattr(model, "parent_chunk_metadata", {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}

    semantic = metadata.get("table_semantic")
    if isinstance(semantic, dict):
        source_start = semantic.get("table_block_index")
        source_end = semantic.get("table_block_index")
        group_index = semantic.get("group_index", 0)
    else:
        source_start = metadata.get("source_block_start", metadata.get("parent_chunk_number"))
        source_end = metadata.get("source_block_end", source_start)
        group_index = metadata.get("parent_chunk_number", 0)

    try:
        source_start_int = int(source_start)
    except (TypeError, ValueError):
        source_start_int = 10**9
    try:
        source_end_int = int(source_end)
    except (TypeError, ValueError):
        source_end_int = source_start_int
    try:
        group_index_int = int(group_index)
    except (TypeError, ValueError):
        group_index_int = 0

    # Put semantic table chunks at their source block. A standard chunk starting
    # at the same block should remain first for deterministic legacy behavior.
    semantic_rank = 1 if isinstance(semantic, dict) else 0
    return (
        source_start_int,
        semantic_rank,
        source_end_int + group_index_int,
        str(getattr(model, "parent_chunk_id", "")),
    )


def _merge_parent_chunks_by_source_order(
    standard_parent_models: list[Any],
    semantic_parent_models: list[Any],
) -> list[Any]:
    """Merge regular and semantic-table parent chunks in document order."""

    return sorted(
        list(standard_parent_models) + _sort_semantic_parents(list(semantic_parent_models)),
        key=_parent_source_order_key,
    )


def _order_children_by_parent_sequence(
    child_chunks_models: list[Any],
    parent_chunks_models: list[Any],
) -> list[Any]:
    """Order child chunks by their parent sequence, then by original child order."""

    parent_order = {
        str(getattr(parent, "parent_chunk_id", "")): index
        for index, parent in enumerate(parent_chunks_models)
    }

    def _key(model: Any) -> tuple[int, int, str]:
        metadata = getattr(model, "child_chunk_metadata", {}) or {}
        if not isinstance(metadata, dict):
            metadata = {}
        parent_id = str(metadata.get("parent_id") or "")
        child_number = metadata.get("child_chunk_number", 10**9)
        table_slice = metadata.get("table_slice")
        if isinstance(table_slice, dict):
            child_number = table_slice.get("slice_index", child_number)
        try:
            child_number_int = int(child_number)
        except (TypeError, ValueError):
            child_number_int = 10**9
        return (
            parent_order.get(parent_id, 10**9),
            child_number_int,
            str(getattr(model, "child_chunk_id", "")),
        )

    return sorted(child_chunks_models, key=_key)


def _resequence_merged_chunk_numbers(
    parent_chunks_models: list[Any],
    child_chunks_models: list[Any],
) -> tuple[list[Any], list[Any]]:
    """Reset parent/child sequence numbers after merging chunk streams."""

    for index, parent_chunk in enumerate(parent_chunks_models):
        parent_meta = (
            parent_chunk.parent_chunk_metadata
            if isinstance(getattr(parent_chunk, "parent_chunk_metadata", None), dict)
            else {}
        )
        parent_meta["parent_chunk_number"] = index
        parent_chunk.parent_chunk_metadata = parent_meta

    for index, child_chunk in enumerate(child_chunks_models):
        child_meta = (
            child_chunk.child_chunk_metadata
            if isinstance(getattr(child_chunk, "child_chunk_metadata", None), dict)
            else {}
        )
        child_meta["child_chunk_number"] = index
        child_chunk.child_chunk_metadata = child_meta

    return parent_chunks_models, child_chunks_models


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
        # Docling-PDF-chunker 5: Run semantic-table preprocessing for PDF tables.
        # Per-table LLM failures are demoted to warnings inside process_semantic_tables_for_pdf,
        # so this call only raises on unrecoverable structural errors.
        transformed_blocks, semantic_parent_models, semantic_child_models, semantic_warnings = (
            process_semantic_tables_for_pdf(
                blocks=parse_result.structured_blocks,
                file_name=file_name,
                file_id=file_id,
                artifact_dir=artifact_dir,
            )
        )

        # Docling-PDF-chunker 6: Convert transformed structured blocks into standard parent/child chunk models.
        standard_parent_models, standard_child_models = (
            split_parent_child_chunks_from_docling_blocks(
                blocks=transformed_blocks,
                file_name=file_name,
                artifact_dir=artifact_dir,
                file_id=file_id,
            )
        )

        # Docling-PDF-chunker 7: Merge standard and semantic chunk streams in source order and re-sequence numbers.
        parent_chunks_models = _merge_parent_chunks_by_source_order(
            list(standard_parent_models),
            list(semantic_parent_models),
        )
        child_chunks_models = _order_children_by_parent_sequence(
            list(standard_child_models) + _sort_semantic_children(list(semantic_child_models)),
            parent_chunks_models,
        )
        parent_chunks_models, child_chunks_models = _resequence_merged_chunk_numbers(
            parent_chunks_models,
            child_chunks_models,
        )

        if semantic_warnings:
            parse_result.warnings.extend(semantic_warnings)
    except Exception as exc:
        # Docling-PDF-chunker 6a: Normalize chunking failure for router-level HTTP mapping.
        raise DoclingChunkingFailedError(str(exc)) from exc

    # Docling-PDF-chunker 8: Convert chunk models into dictionaries for storage/upsert.
    parent_chunks_dicts = [chunk.model_dump() for chunk in parent_chunks_models]
    child_chunks_dicts = [chunk.model_dump() for chunk in child_chunks_models]

    # Docling-PDF-chunker 9: Aggregate parse warnings and partial-failure summary.
    warnings = list(parse_result.warnings)
    if parse_result.partial_failures:
        warnings.append(
            f"Docling reported {len(parse_result.partial_failures)} partial failure chunk(s)."
        )

    # Docling-PDF-chunker 10: Return chunk payloads, warnings, and Docling run id for logging.
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
    user_id: str,
) -> None:
    """Upsert parent/child chunks into vector storage and normalize upsert failures."""
    try:
        await upsert_documents(
            parent_chunks=parent_chunks,
            child_chunks=child_chunks,
            user_id=user_id,
        )
    except Exception as exc:
        raise UpsertChunksFailedError("upsert to vector store failed") from exc


def run_ingest_upload_sync(
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


async def run_ingest_upload(
    *,
    file_name: str,
    content_type: str,
    data: str,
) -> dict[str, Any]:
    """
    Async wrapper that moves CPU/blocking parsing and chunking work off the event loop.
    """
    return await asyncio.to_thread(
        run_ingest_upload_sync,
        file_name=file_name,
        content_type=content_type,
        data=data,
    )
