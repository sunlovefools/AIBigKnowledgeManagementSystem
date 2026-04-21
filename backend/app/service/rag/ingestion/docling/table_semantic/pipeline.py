"""
Semantic-table preprocessing for Docling PDF ingestion.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.id_utils import generate_uuid_v6
from app.service.rag.ingestion.chunk_models import ChildChunkModel, ParentChunkModel
from app.service.rag.ingestion.docling.models import DoclingStructuredBlock

from . import config, llm_client, prompts
from .markdown_table import (
    build_table_sample_markdown,
    flatten_layout_table_to_bullets,
    parse_markdown_table,
    render_markdown_table_rows,
)
from .models import ParsedMarkdownTable, TableClassification


class TableSemanticIngestionError(RuntimeError):
    pass


def _resolve_page_number(page_no: int | None) -> int:
    if isinstance(page_no, int) and page_no > 0:
        return page_no
    return 0


def _nearest_text_context(
    blocks: list[DoclingStructuredBlock],
    block_index: int,
    *,
    direction: str,
) -> str:
    """Find the nearest text or list block content in the specified direction from the given block index, and return it as a single-line string. If no such block is found within a reasonable range, return an empty string."""
    step = -1 if direction == "previous" else 1
    idx = block_index + step
    while 0 <= idx < len(blocks):
        block = blocks[idx]
        #TODO: Allow header to be part of it too. If its header, then we + the next block until it is no longer header
        if block.block_type in {"text", "list"}:
            return " ".join((block.content or "").split())[:2000]
        idx += step
    return ""


def _normalize_classification_payload(payload: Any) -> TableClassification:
    """
    Normalize and validate the raw classifier output payload, ensuring it has the expected structure and types, and return a TableClassification object."""
    if not isinstance(payload, dict):
        raise TableSemanticIngestionError(
            "Table classifier returned non-object JSON payload."
        )

    # Normalise classification output 1: Extract and validate the 'type' field, which is required and must be one of the expected table types.
    raw_type = str(payload.get("type") or "").strip().lower()
    if raw_type not in {"layout", "matrix", "entity_list"}:
        raise TableSemanticIngestionError(
            f"Table classifier returned invalid type: {raw_type!r}"
        )

    # Normalise classification output 2: Extract and validate the 'needs_description' field, which is optional and defaults to False.
    raw_needs_description = payload.get("needs_description")
    needs_description = bool(raw_needs_description)
    if raw_type == "layout":
        needs_description = False

    # Normalise classification output 3: Extract and validate the 'col_headers' and 'row_headers' fields, which are optional lists of strings. 
    # If present, they must be lists, and their items must be non-empty strings.
    raw_col_headers = payload.get("col_headers")
    raw_row_headers = payload.get("row_headers")

    # Normalise classification output 4: Build the final TableClassification object with normalized values.
    col_headers = (
        [str(item).strip() for item in raw_col_headers if str(item).strip()]
        if isinstance(raw_col_headers, list)
        else []
    )
    row_headers = (
        [str(item).strip() for item in raw_row_headers if str(item).strip()]
        if isinstance(raw_row_headers, list)
        else []
    )

    return TableClassification(
        table_type=raw_type,  # type: ignore[arg-type]
        needs_description=needs_description,
        col_headers=col_headers,
        row_headers=row_headers,
    )


def _normalize_row_slice_summaries(payload: Any, expected_count: int) -> list[str]:
    """
    Normalize and validate the raw row-slice summary output payload, ensuring it is a list of objects with 'slice_index' and 'summary' fields,
      and return a list of summaries ordered by slice index. The payload must contain exactly one summary for each slice index from 0 to expected_count-1, otherwise an error is raised."""
    if not isinstance(payload, list):
        raise TableSemanticIngestionError(
            "Row-summary call did not return a JSON array."
        )

    summaries_by_index: dict[int, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        raw_index = item.get("slice_index")
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        try:
            idx = int(raw_index)
        except (TypeError, ValueError):
            continue
        if idx < 0:
            continue
        summaries_by_index[idx] = summary

    normalized: list[str] = []
    for idx in range(expected_count):
        summary = summaries_by_index.get(idx)
        if not summary:
            raise TableSemanticIngestionError(
                f"Missing row-slice summary for slice_index={idx}."
            )
        normalized.append(summary)
    return normalized


def _llm_structured_json_call(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> Any:
    """Call LLM and parse response as JSON, with error handling for common issues like non-JSON response or missing fields."""
    content = llm_client.chat_completion(
        url=config.get_table_semantic_llm_url(),
        api_key=config.get_table_semantic_llm_api_key(),
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        timeout_s=config.get_timeout_seconds(),
    )
    return llm_client.parse_json_response(content)


def _llm_text_call(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """Call LLM and return raw text response, with error handling for API call issues."""
    return llm_client.chat_completion(
        url=config.get_table_semantic_llm_url(),
        api_key=config.get_table_semantic_llm_api_key(),
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        timeout_s=config.get_timeout_seconds(),
    ).strip()


def _classify_table(
    *,
    parsed_table: ParsedMarkdownTable,
    context_before: str,
    context_after: str,
) -> TableClassification:
    """Classify the table using LLM, with context and sample markdown for better accuracy."""

    # Classify table 1: Build sample markdown for the table and call LLM classifier with context and sample markdown.
    sample_markdown = build_table_sample_markdown(
        parsed_table,
        sample_rows=config.get_max_sample_rows(),
    )

    # Classify table 2: Normalize and validate the classifier output, and return classification result.
    payload = _llm_structured_json_call(
        model=config.get_classifier_model(),
        system_prompt=prompts.CLASSIFIER_SYSTEM_PROMPT,
        user_prompt=prompts.build_classifier_user_prompt(
            context_before=context_before,
            table_markdown_sample=sample_markdown,
            context_after=context_after,
        ),
    )

    # Classify table 3: Normalize and validate the classifier output, and return classification result.
    return _normalize_classification_payload(payload)


def _build_row_slice_summaries(
    *,
    parsed_table: ParsedMarkdownTable,
    general_description: str,
    child_rows_per_chunk: int,
) -> list[str]:
    """
    Build row-slice summaries by pre-slicing rows in code, calling LLM per
    parent-sized batch with explicit slice blocks, and normalizing results.
    """
    if not parsed_table.rows:
        return []

    # Keep row-summary calls aligned to parent grouping windows (3 child slices).
    rows_per_parent = child_rows_per_chunk * 3
    all_summaries: list[str] = []

    # For each parent-sized batch, pre-slice rows and pass explicit slices to the LLM.
    row_lines = parsed_table.markdown_rows
    for batch_start in range(0, len(row_lines), rows_per_parent):
        batch_rows = row_lines[batch_start : batch_start + rows_per_parent]
        if not batch_rows:
            continue

        explicit_slice_blocks: list[str] = []
        for local_slice_index, row_start in enumerate(
            range(0, len(batch_rows), child_rows_per_chunk)
        ):
            slice_rows = batch_rows[row_start : row_start + child_rows_per_chunk]
            slice_markdown = render_markdown_table_rows(
                header_line=parsed_table.markdown_header,
                separator_line=parsed_table.markdown_separator,
                row_lines=slice_rows,
            )
            explicit_slice_blocks.append(
                f"Slice {local_slice_index}:\n{slice_markdown}"
            )

        batch_slice_count = len(explicit_slice_blocks)
        explicit_slices_markdown = "\n\n".join(explicit_slice_blocks)
        payload = _llm_structured_json_call(
            model=config.get_row_model(),
            system_prompt=prompts.ROW_SUMMARY_SYSTEM_PROMPT,
            user_prompt=prompts.build_row_summary_user_prompt(
                general_description=general_description,
                explicit_slices_markdown=explicit_slices_markdown,
                slice_size=child_rows_per_chunk,
                expected_slice_count=batch_slice_count,
            ),
        )
        batch_summaries = _normalize_row_slice_summaries(payload, batch_slice_count)
        all_summaries.extend(batch_summaries)

    expected_total_slices = math.ceil(len(row_lines) / child_rows_per_chunk)
    if len(all_summaries) != expected_total_slices:
        raise TableSemanticIngestionError(
            "Row-summary output size mismatch: "
            f"expected={expected_total_slices}, actual={len(all_summaries)}"
        )
    return all_summaries


def _build_semantic_chunks_for_table(
    *,
    table_id: str,
    table_block: DoclingStructuredBlock,
    parsed_table: ParsedMarkdownTable,
    classification: TableClassification,
    general_description: str,
    row_slice_summaries: list[str],
    file_name: str,
    file_id: str,
) -> tuple[list[ParentChunkModel], list[ChildChunkModel]]:
    headers = (
        classification.col_headers
        if classification.col_headers
        else parsed_table.headers
    )
    child_rows_per_chunk = 10 if len(headers) <= 10 else 5

    # Build semantic child payloads first.
    child_payloads: list[dict[str, Any]] = []
    total_rows = len(parsed_table.markdown_rows)
    if total_rows == 0:
        return [], []

    for slice_index, row_start in enumerate(range(0, total_rows, child_rows_per_chunk)):
        row_end = min(row_start + child_rows_per_chunk, total_rows)
        slice_rows = parsed_table.markdown_rows[row_start:row_end]
        if not slice_rows:
            continue
        if slice_index >= len(row_slice_summaries):
            raise TableSemanticIngestionError(
                f"Missing semantic summary for slice_index={slice_index}."
            )

        rows_markdown = render_markdown_table_rows(
            header_line=parsed_table.markdown_header,
            separator_line=parsed_table.markdown_separator,
            row_lines=slice_rows,
        )
        headers_markdown = render_markdown_table_rows(
            header_line=parsed_table.markdown_header,
            separator_line=parsed_table.markdown_separator,
            row_lines=[],
        )
        child_payloads.append(
            {
                "slice_index": slice_index,
                "row_start": row_start + 1,  # 1-based for readability
                "row_end": row_end,
                "rows_markdown": rows_markdown,
                "summary": row_slice_summaries[slice_index],
                "headers_markdown": headers_markdown,
                "row_lines": slice_rows,
            }
        )

    parent_chunks: list[ParentChunkModel] = []
    child_chunks: list[ChildChunkModel] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    page_number = _resolve_page_number(table_block.page_no)

    for group_idx, child_start in enumerate(range(0, len(child_payloads), 3)):
        grouped = child_payloads[child_start : child_start + 3]
        parent_id = generate_uuid_v6()
        parent_child_ids: list[str] = []

        parent_row_lines: list[str] = []
        for payload in grouped:
            parent_row_lines.extend(payload["row_lines"])
        parent_content = render_markdown_table_rows(
            header_line=parsed_table.markdown_header,
            separator_line=parsed_table.markdown_separator,
            row_lines=parent_row_lines,
        )

        for payload in grouped:
            child_id = generate_uuid_v6()
            parent_child_ids.append(child_id)
            child_content = "\n\n".join(
                [
                    f"General Description: {general_description}",
                    f"Row-Specific Description: {payload['summary']}",
                    f"Headers:\n{payload['headers_markdown']}",
                    f"Rows:\n{payload['rows_markdown']}",
                ]
            ).strip()
            child_chunks.append(
                ChildChunkModel(
                    child_chunk_id=child_id,
                    content=child_content,
                    file_metadata={
                        "file_name": file_name,
                        "file_id": file_id,
                    },
                    child_chunk_metadata={
                        "parent_id": parent_id,
                        "child_chunk_number": 0,  # re-sequenced in ingest service
                        "page_number": page_number,
                        "has_preamble": False,
                        "ingested_at": now_iso,
                        "table_slice": {
                            "table_id": table_id,
                            "slice_index": int(payload["slice_index"]),
                            "row_start": int(payload["row_start"]),
                            "row_end": int(payload["row_end"]),
                            "table_block_index": int(table_block.block_index),
                        },
                    },
                    content_flags={
                        "is_image": False,
                        "is_table_image": False,
                        "is_semantic_table": True,
                    },
                    artifact_refs={
                        "image_uuid": None,
                        "table_image_uuid": None,
                    },
                )
            )

        parent_chunks.append(
            ParentChunkModel(
                parent_chunk_id=parent_id,
                content=parent_content,
                file_metadata={
                    "file_name": file_name,
                    "file_id": file_id,
                },
                parent_chunk_metadata={
                    "child_chunks_ids": parent_child_ids,
                    "parent_chunk_number": 0,  # re-sequenced in ingest service
                    "page_number": [page_number] if page_number > 0 else [0],
                    "ingested_at": now_iso,
                    "table_semantic": {
                        "table_id": table_id,
                        "table_type": classification.table_type,
                        "col_headers": headers,
                        "row_headers": classification.row_headers,
                        "general_description": general_description,
                        "group_index": group_idx,
                        "table_block_index": int(table_block.block_index),
                        "child_rows_per_chunk": child_rows_per_chunk,
                        "parent_group_size": 3,
                    },
                },
                content_flags={
                    "is_image": False,
                    "is_table_image": False,
                    "is_semantic_table": True,
                },
                artifact_refs={
                    "image_uuid": [],
                    "table_image_uuid": [],
                },
            )
        )

    return parent_chunks, child_chunks


def _write_semantic_artifact(
    *,
    artifact_dir: Path | None,
    diagnostics: list[dict[str, Any]],
) -> None:
    if artifact_dir is None:
        return
    try:
        output_path = artifact_dir / "table_semantic_results.json"
        output_path.write_text(
            json.dumps({"tables": diagnostics}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        # Artifact persistence is best-effort only.
        return


def process_semantic_tables_for_pdf(
    *,
    blocks: list[DoclingStructuredBlock],
    file_name: str,
    file_id: str,
    artifact_dir: Path | None,
) -> tuple[list[DoclingStructuredBlock], list[ParentChunkModel], list[ChildChunkModel], list[str]]:
    """
    Process Docling table blocks with semantic-table pipeline.

    Returns:
    - transformed blocks for standard chunking
    - semantic parent chunks
    - semantic child chunks
    - warnings
    """

    # Process Semantic Tables 1: if the feature flag is off, skip all processing and return original blocks
    if not config.is_table_semantic_ingestion_enabled():
        return list(blocks), [], [], []

    transformed_blocks = list(blocks)
    semantic_parents: list[ParentChunkModel] = []
    semantic_children: list[ChildChunkModel] = []
    warnings: list[str] = []
    diagnostics: list[dict[str, Any]] = []

    # Process Semantic Tables 2: iterate through blocks to find tables that are eligible for semantic processing, 
    # classify and build semantic chunks for them, and gather diagnostics along the way.
    for index, block in enumerate(blocks):
        if block.block_type != "table" or block.is_table_image:
            continue
        
        # Process Semantic Tables 2.1: Parse the markdown table and skip if parsing fails
        parsed = parse_markdown_table(block.content or "")
        if parsed is None:
            transformed_blocks[index] = block.model_copy(
                update={"block_type": "text", "content": block.content}
            )
            diagnostics.append(
                {
                    "block_index": int(block.block_index),
                    "page_no": _resolve_page_number(block.page_no),
                    "status": "parse_failed_treated_as_layout",
                }
            )
            continue
        
        # Process Semantic Tables 2.2: For successfully parsed tables, get the previous and next text contexts to provide to the classifier, to which is to classify the table.
        context_before = _nearest_text_context(blocks, index, direction="previous")
        context_after = _nearest_text_context(blocks, index, direction="next")

        # Process Semantic Tables 2.3: If classification fails, skip and treat as layout table. 
        # If classification succeeds and is classified as layout or doesn't need description, flatten to bullets. 
        # If classification succeeds and is classified as matrix/entity_list and needs description, generate general description and row-slice summaries, build semantic chunks, and remove raw table from default chunking path.
        try:
            classification = _classify_table(
                parsed_table=parsed,
                context_before=context_before,
                context_after=context_after,
            )
        except Exception as exc:
            raise TableSemanticIngestionError(
                "Table semantic classification failed "
                f"for block_index={block.block_index}: {exc}"
            ) from exc

        # Process Semantic Tables 2.4: If classification succeeds and is classified as layout or doesn't need description, flatten to bullets.
        if classification.table_type == "layout" or not classification.needs_description:
            flattened = flatten_layout_table_to_bullets(parsed)
            transformed_blocks[index] = block.model_copy(
                update={
                    "block_type": "text",
                    "content": flattened,
                }
            )
            diagnostics.append(
                {
                    "block_index": int(block.block_index),
                    "page_no": _resolve_page_number(block.page_no),
                    "status": "layout_flattened",
                    "classification": classification.table_type,
                    "col_headers": classification.col_headers,
                    "row_headers": classification.row_headers,
                }
            )
            continue

        # Process Semantic Tables 2.5: If classification succeeds and is classified as matrix or entity_list, 
        # proceed with semantic chunking.
        table_id = generate_uuid_v6()
        try:
            general_description = _llm_text_call(
                model=config.get_global_model(),
                system_prompt=prompts.GLOBAL_DESCRIPTION_SYSTEM_PROMPT,
                user_prompt=prompts.build_global_description_user_prompt(
                    col_headers=classification.col_headers or parsed.headers,
                    row_headers=classification.row_headers,
                    context_before=context_before,
                    context_after=context_after,
                ),
            )
        except Exception as exc:
            raise TableSemanticIngestionError(
                "Global table description generation failed "
                f"for block_index={block.block_index}: {exc}"
            ) from exc

        child_rows_per_chunk = (
            10 if len(classification.col_headers or parsed.headers) <= 10 else 5
        )

        try:
            row_summaries = _build_row_slice_summaries(
                parsed_table=parsed,
                general_description=general_description,
                child_rows_per_chunk=child_rows_per_chunk,
            )
        except Exception as exc:
            raise TableSemanticIngestionError(
                "Row-slice summary generation failed "
                f"for block_index={block.block_index}: {exc}"
            ) from exc

        table_parents, table_children = _build_semantic_chunks_for_table(
            table_id=table_id,
            table_block=block,
            parsed_table=parsed,
            classification=classification,
            general_description=general_description,
            row_slice_summaries=row_summaries,
            file_name=file_name,
            file_id=file_id,
        )
        semantic_parents.extend(table_parents)
        semantic_children.extend(table_children)

        # Remove raw matrix/entity_list table from default chunking path.
        transformed_blocks[index] = block.model_copy(
            update={"block_type": "other", "content": ""}
        )

        diagnostics.append(
            {
                "table_id": table_id,
                "block_index": int(block.block_index),
                "page_no": _resolve_page_number(block.page_no),
                "status": "semantic_chunked",
                "classification": classification.table_type,
                "col_headers": classification.col_headers or parsed.headers,
                "row_headers": classification.row_headers,
                "child_rows_per_chunk": child_rows_per_chunk,
                "semantic_parent_count": len(table_parents),
                "semantic_child_count": len(table_children),
            }
        )

    _write_semantic_artifact(artifact_dir=artifact_dir, diagnostics=diagnostics)
    return transformed_blocks, semantic_parents, semantic_children, warnings
