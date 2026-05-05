"""
Semantic-table preprocessing for Docling PDF ingestion.
"""

from __future__ import annotations

import json
import math
import re
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
from .models import DescriptionAndSections, ParsedMarkdownTable, TableClassification


class TableSemanticIngestionError(RuntimeError):
    pass


_WEIGHT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%")
_AGGREGATE_LABEL_RE = re.compile(
    r"\b(grand\s+total|subtotal|sub-total|total|overall|sum|aggregate)\b",
    re.IGNORECASE,
)
_CRITERIA_MARKER_RE = re.compile(r"\b(criteria|requirements?|components?|items?)\s*:", re.IGNORECASE)

def _normalize_space(value: str) -> str:
    return " ".join((value or "").split())


def _row_text(row: list[str]) -> str:
    return _normalize_space(" ".join(cell for cell in row if cell))


def _first_meaningful_cell(row: list[str]) -> str:
    for cell in row:
        text = _normalize_space(cell)
        if text:
            return text
    return ""


def _clean_section_name(value: str) -> str:
    text = _normalize_space(value)
    if not text:
        return ""
    text = re.sub(r"(?i)\b(grand\s+total|subtotal|sub-total|total)\s*:?\s*$", "", text).strip(" -:;,.")
    text = re.sub(r"\s+", " ", text)
    return text


def _is_pure_aggregate_row(row: list[str]) -> bool:
    """Return True for rows that are only totals/subtotals/scores, not topics."""

    non_empty = [_normalize_space(cell) for cell in row if _normalize_space(cell)]
    if not non_empty:
        return False
    joined = " ".join(non_empty)
    alpha_words = re.findall(r"[A-Za-z]+", joined)
    has_aggregate_label = bool(_AGGREGATE_LABEL_RE.search(joined))
    has_substantive_marker = bool(_CRITERIA_MARKER_RE.search(joined))
    long_text_cells = [cell for cell in non_empty if len(cell) > 80]
    mostly_numeric = len(alpha_words) <= 3 and bool(_WEIGHT_RE.search(joined))
    return has_aggregate_label and not has_substantive_marker and (mostly_numeric or not long_text_cells)


def _dense_item_signals(row: list[str]) -> list[str]:
    text = _row_text(row)
    signals: list[str] = []
    if len(text) > 600:
        signals.append("long_row_text")
    if len(_WEIGHT_RE.findall(text)) >= 2:
        signals.append("multiple_weights")
    if len(_CRITERIA_MARKER_RE.findall(text)) >= 2:
        signals.append("multiple_item_markers")
    if any(len(_normalize_space(cell)) > 320 for cell in row):
        signals.append("long_cell")
    if text.count(";") >= 4:
        signals.append("many_semicolon_phrases")
    return signals


def _section_density_summary(rows: list[list[str]]) -> dict[str, Any]:
    dense_rows: list[dict[str, Any]] = []
    for local_index, row in enumerate(rows):
        signals = _dense_item_signals(row)
        if signals:
            dense_rows.append(
                {
                    "local_row_index": local_index,
                    "signals": signals,
                    "preview": _row_text(row)[:240],
                }
            )
    return {
        "has_dense_items": bool(dense_rows),
        "dense_row_count": len(dense_rows),
        "dense_rows": dense_rows[:8],
        "item_extraction_recommended": bool(dense_rows),
    }


def _extract_weights(row: list[str]) -> list[str]:
    weights: list[str] = []
    for cell in row:
        for match in _WEIGHT_RE.findall(cell or ""):
            normalized = match.replace(" ", "")
            if normalized not in weights:
                weights.append(normalized)
    return weights


def _cells_by_header(headers: list[str], row: list[str]) -> dict[str, str]:
    cells: dict[str, str] = {}
    for idx, value in enumerate(row):
        header = _normalize_space(headers[idx] if idx < len(headers) else "")
        if not header:
            header = f"column_{idx + 1}"
        cell_value = _normalize_space(value)
        if cell_value:
            cells[header] = cell_value
    return cells


def _extract_row_label(row: list[str], exclude_text: str | list[str]) -> str | None:
    """Extract a meaningful label from a data row, skipping the section name itself."""
    exclude_values = exclude_text if isinstance(exclude_text, list) else [exclude_text]
    exclude_lowers = {
        _normalize_space(value).lower()
        for value in exclude_values
        if _normalize_space(value)
    }
    for cell in row:
        text = _normalize_space(cell)
        if not text or text.lower() in exclude_lowers:
            continue
        if not re.search(r"[A-Za-z]", text):
            continue
        text = _WEIGHT_RE.sub("", text).strip(" -:;,.")
        if text and text.lower() not in exclude_lowers:
            return text
    return None


def _build_structured_rows(
    *,
    parsed_table: ParsedMarkdownTable,
    headers: list[str],
    row_indices: list[int],
    section_name: str,
    parent_section_name: str | None = None,
    subsection_name: str | None = None,
    criteria_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build a generic row-level view for structure-preserving retrieval."""

    label_excludes = [section_name]
    if parent_section_name:
        label_excludes.append(parent_section_name)
    if subsection_name:
        label_excludes.append(subsection_name)

    supplied_labels = [
        _normalize_space(label)
        for label in (criteria_names or [])
        if _normalize_space(label)
    ]
    one_label_per_row = len(supplied_labels) == len(row_indices)

    structured_rows: list[dict[str, Any]] = []
    for local_idx, row_idx in enumerate(row_indices):
        if row_idx < 0 or row_idx >= len(parsed_table.rows):
            continue
        row = parsed_table.rows[row_idx]
        inferred_label = _extract_row_label(row, label_excludes)
        row_label = (
            supplied_labels[local_idx]
            if one_label_per_row
            else (inferred_label or (supplied_labels[0] if len(row_indices) == 1 and supplied_labels else ""))
        )
        structured_rows.append(
            {
                "row_index": row_idx,
                "label": row_label,
                "weights": _extract_weights(row),
                "cells": _cells_by_header(headers, row),
                "text": _row_text(row),
            }
        )
    return structured_rows


def _normalize_label_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    labels: list[str] = []
    for item in value:
        text = _normalize_space(str(item or ""))
        if text and text not in labels:
            labels.append(text)
    return labels


def _normalize_section_detection_payload(
    payload: Any,
    total_rows: int,
) -> list[dict[str, Any]]:
    """Validate and normalise the LLM section-detection JSON response."""
    if not isinstance(payload, dict) or not payload.get("has_sections"):
        return []
    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, list):
        return []

    result: list[dict[str, Any]] = []
    seen: set[int] = set()

    def _normalize_header(item: dict[str, Any]) -> dict[str, Any] | None:
        try:
            row_index = int(item["row_index"])
        except (KeyError, TypeError, ValueError):
            return None
        section_name = _clean_section_name(str(item.get("section_name") or ""))
        if not section_name or row_index < 0 or row_index >= total_rows:
            return None

        subsections: list[dict[str, Any]] = []
        raw_subsections = item.get("subsections")
        if isinstance(raw_subsections, list):
            subsection_seen: set[int] = set()
            for raw_subsection in raw_subsections:
                if not isinstance(raw_subsection, dict):
                    continue
                normalized_subsection = _normalize_header(raw_subsection)
                if normalized_subsection is None:
                    continue
                subsection_row = int(normalized_subsection["row_index"])
                if subsection_row in subsection_seen:
                    continue
                subsection_seen.add(subsection_row)
                subsections.append(normalized_subsection)

        subsections.sort(key=lambda x: x["row_index"])
        return {
            "row_index": row_index,
            "section_name": section_name,
            "row_labels": _normalize_label_list(item.get("row_labels")),
            "subsections": subsections,
        }

    for item in raw_sections:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_header(item)
        if normalized is None:
            continue
        row_index = int(normalized["row_index"])
        if row_index in seen:
            continue
        seen.add(row_index)
        result.append(normalized)

    result.sort(key=lambda x: x["row_index"])
    return result


def _normalize_description_and_sections_payload(
    payload: Any,
    total_rows: int,
) -> DescriptionAndSections:
    """Validate the combined table-description and section-detection response."""
    if not isinstance(payload, dict):
        raise TableSemanticIngestionError(
            "Description-and-sections output must be a JSON object."
        )
    description = _normalize_space(str(payload.get("description") or ""))
    if not description:
        raise TableSemanticIngestionError(
            "Description-and-sections output is missing description."
        )
    sections = _normalize_section_detection_payload(payload, total_rows)
    return DescriptionAndSections(description=description, sections=sections)


def _build_description_and_sections(
    *,
    parsed_table: ParsedMarkdownTable,
    classification: TableClassification,
    context_before: str,
    context_after: str,
) -> DescriptionAndSections:
    sample_markdown = build_table_sample_markdown(
        parsed_table,
        sample_rows=config.get_max_sample_rows(),
    )
    payload = _llm_structured_json_call(
        model=config.get_global_model(),
        system_prompt=prompts.DESCRIPTION_AND_SECTIONS_SYSTEM_PROMPT,
        user_prompt=prompts.build_description_and_sections_user_prompt(
            col_headers=classification.col_headers or parsed_table.headers,
            row_headers=classification.row_headers,
            context_before=context_before,
            context_after=context_after,
            table_sample_markdown=sample_markdown,
        ),
    )
    return _normalize_description_and_sections_payload(payload, len(parsed_table.rows))


def _section_headers_to_spans(
    *,
    parsed_table: ParsedMarkdownTable,
    section_headers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(section_headers) < 2:
        return []

    spans: list[dict[str, Any]] = []

    def _build_span(
        *,
        section_index: int,
        section_name: str,
        start: int,
        end: int,
        supplied_labels: list[str],
        parent_section_name: str | None = None,
        subsection_name: str | None = None,
    ) -> dict[str, Any] | None:
        if end <= start:
            return None
        row_indices = list(range(start, end))
        criteria_names: list[str] = list(supplied_labels)
        weights: list[str] = []
        should_infer_labels = not criteria_names
        label_excludes = [section_name]
        if parent_section_name:
            label_excludes.append(parent_section_name)
        if subsection_name:
            label_excludes.append(subsection_name)
        for row_idx in row_indices:
            row = parsed_table.rows[row_idx]
            if should_infer_labels:
                label = _extract_row_label(row, label_excludes)
                if label and label not in criteria_names:
                    criteria_names.append(label)
            for w in _extract_weights(row):
                if w not in weights:
                    weights.append(w)
        return {
            "section_index": section_index,
            "section_name": section_name,
            "parent_section_name": parent_section_name,
            "subsection_name": subsection_name,
            "row_start": start,
            "row_end": end,
            "row_indices": row_indices,
            "criteria_names": criteria_names,
            "weights": weights,
            "is_subsection": subsection_name is not None,
        }

    for section_index, header in enumerate(section_headers):
        start = header["row_index"]
        if _is_pure_aggregate_row(parsed_table.rows[start]):
            continue
        section_name = _clean_section_name(str(header["section_name"]))
        if not section_name:
            first_cell_name = _clean_section_name(_first_meaningful_cell(parsed_table.rows[start]))
            section_name = first_cell_name
        if not section_name:
            continue
        next_start = (
            section_headers[section_index + 1]["row_index"]
            if section_index + 1 < len(section_headers)
            else len(parsed_table.rows)
        )
        if next_start <= start:
            continue

        raw_subsections = [
            subsection
            for subsection in header.get("subsections", [])
            if start <= int(subsection.get("row_index", -1)) < next_start
        ]
        raw_subsections.sort(key=lambda x: x["row_index"])
        if len(raw_subsections) >= 2:
            for subsection_index, subsection in enumerate(raw_subsections):
                subsection_start = int(subsection["row_index"])
                if _is_pure_aggregate_row(parsed_table.rows[subsection_start]):
                    continue
                subsection_end = (
                    int(raw_subsections[subsection_index + 1]["row_index"])
                    if subsection_index + 1 < len(raw_subsections)
                    else next_start
                )
                subsection_name = _clean_section_name(str(subsection["section_name"]))
                if not subsection_name:
                    subsection_name = _clean_section_name(
                        _first_meaningful_cell(parsed_table.rows[subsection_start])
                    )
                if not subsection_name:
                    continue
                span = _build_span(
                    section_index=len(spans),
                    section_name=f"{section_name} / {subsection_name}",
                    start=subsection_start,
                    end=subsection_end,
                    supplied_labels=list(subsection.get("row_labels") or []),
                    parent_section_name=section_name,
                    subsection_name=subsection_name,
                )
                if span is not None:
                    spans.append(span)
            continue

        span = _build_span(
            section_index=len(spans),
            section_name=section_name,
            start=start,
            end=next_start,
            supplied_labels=list(header.get("row_labels") or []),
        )
        if span is not None:
            spans.append(span)

    return spans


def _build_section_semantic_chunks_for_table(
    *,
    table_id: str,
    table_block: DoclingStructuredBlock,
    parsed_table: ParsedMarkdownTable,
    classification: TableClassification,
    general_description: str,
    section_spans: list[dict[str, Any]],
    child_rows_per_chunk: int,
    file_name: str,
    file_id: str,
) -> tuple[list[ParentChunkModel], list[ChildChunkModel]]:
    headers = (
        classification.col_headers
        if classification.col_headers
        else parsed_table.headers
    )
    parent_chunks: list[ParentChunkModel] = []
    child_chunks: list[ChildChunkModel] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    page_number = _resolve_page_number(table_block.page_no)
    section_single_chunk_max_rows = config.get_section_single_chunk_max_rows()

    for section in section_spans:
        row_indices = section["row_indices"]
        row_lines = [parsed_table.markdown_rows[idx] for idx in row_indices]
        # section_markdown already includes the header row + separator + data rows.
        section_markdown = render_markdown_table_rows(
            header_line=parsed_table.markdown_header,
            separator_line=parsed_table.markdown_separator,
            row_lines=row_lines,
        )
        section_name = section["section_name"]
        criteria_names = section["criteria_names"]
        weights = section["weights"]
        section_rows = [parsed_table.rows[idx] for idx in row_indices]
        density = _section_density_summary(section_rows)
        structured_rows = _build_structured_rows(
            parsed_table=parsed_table,
            headers=headers,
            row_indices=row_indices,
            section_name=section_name,
            parent_section_name=section.get("parent_section_name"),
            subsection_name=section.get("subsection_name"),
            criteria_names=criteria_names,
        )
        criteria_line = ", ".join(criteria_names) if criteria_names else "none detected"
        weights_line = ", ".join(weights) if weights else "none detected"

        parent_id = generate_uuid_v6()
        # Parent content is what the source viewer reconstructs. Keep it close to
        # the uploaded document instead of exposing retrieval-only semantic labels.
        parent_content = section_markdown.strip()

        child_ids: list[str] = []
        is_large_section = len(row_lines) > section_single_chunk_max_rows

        if is_large_section:
            row_summaries = _build_row_slice_summaries(
                parsed_table=parsed_table,
                general_description=general_description,
                child_rows_per_chunk=child_rows_per_chunk,
                row_lines=row_lines,
            )
            for local_slice_index, local_row_start in enumerate(
                range(0, len(row_lines), child_rows_per_chunk)
            ):
                local_row_end = min(local_row_start + child_rows_per_chunk, len(row_lines))
                slice_rows = row_lines[local_row_start:local_row_end]
                slice_markdown = render_markdown_table_rows(
                    header_line=parsed_table.markdown_header,
                    separator_line=parsed_table.markdown_separator,
                    row_lines=slice_rows,
                )
                child_id = generate_uuid_v6()
                child_ids.append(child_id)
                child_content = "\n\n".join(
                    [
                        f"Section: {section_name}",
                        f"General Description: {general_description}",
                        f"Row Description: {row_summaries[local_slice_index]}",
                        f"Criteria Names: {criteria_line}",
                        f"Weights: {weights_line}",
                        f"Table:\n{slice_markdown}",
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
                                "slice_index": int(section["section_index"]),
                                "local_slice_index": int(local_slice_index),
                                "row_start": int(section["row_start"]) + local_row_start + 1,
                                "row_end": int(section["row_start"]) + local_row_end,
                                "table_block_index": int(table_block.block_index),
                                "section_chunking": True,
                                "large_section_chunking": True,
                                "section_index": int(section["section_index"]),
                                "section_name": section_name,
                                "parent_section_name": section.get("parent_section_name"),
                                "subsection_name": section.get("subsection_name"),
                                "is_subsection": bool(section.get("is_subsection")),
                                "criteria_names": criteria_names,
                                "weights": weights,
                                "density": density,
                                "structured_rows": [
                                    row
                                    for row in structured_rows
                                    if local_row_start <= row["row_index"] - int(section["row_start"]) < local_row_end
                                ],
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
        else:
            child_id = generate_uuid_v6()
            child_ids.append(child_id)
            child_content = "\n\n".join(
                [
                    f"Section: {section_name}",
                    f"General Description: {general_description}",
                    f"Criteria Names: {criteria_line}",
                    f"Weights: {weights_line}",
                    f"Table:\n{section_markdown}",
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
                            "slice_index": int(section["section_index"]),
                            "row_start": int(section["row_start"]) + 1,
                            "row_end": int(section["row_end"]),
                            "table_block_index": int(table_block.block_index),
                            "section_chunking": True,
                            "large_section_chunking": False,
                            "section_index": int(section["section_index"]),
                            "section_name": section_name,
                            "parent_section_name": section.get("parent_section_name"),
                            "subsection_name": section.get("subsection_name"),
                            "is_subsection": bool(section.get("is_subsection")),
                            "criteria_names": criteria_names,
                            "weights": weights,
                            "density": density,
                            "structured_rows": structured_rows,
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
                    "child_chunks_ids": child_ids,
                    "parent_chunk_number": 0,  # re-sequenced in ingest service
                    "page_number": [page_number] if page_number > 0 else [0],
                    "ingested_at": now_iso,
                    "table_semantic": {
                        "table_id": table_id,
                        "table_type": classification.table_type,
                        "col_headers": headers,
                        "row_headers": classification.row_headers,
                        "general_description": general_description,
                        "group_index": int(section["section_index"]),
                        "table_block_index": int(table_block.block_index),
                        "child_rows_per_chunk": child_rows_per_chunk if is_large_section else None,
                        "parent_group_size": len(child_ids),
                        "section_chunking": True,
                        "large_section_chunking": is_large_section,
                        "section_single_chunk_max_rows": section_single_chunk_max_rows,
                        "section_index": int(section["section_index"]),
                        "section_name": section_name,
                        "parent_section_name": section.get("parent_section_name"),
                        "subsection_name": section.get("subsection_name"),
                        "is_subsection": bool(section.get("is_subsection")),
                        "section_row_start": int(section["row_start"]) + 1,
                        "section_row_end": int(section["row_end"]),
                        "criteria_names": criteria_names,
                        "weights": weights,
                        "density": density,
                        "structured_rows": structured_rows,
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
    row_lines: list[str] | None = None,
) -> list[str]:
    """
    Build row-slice summaries by pre-slicing rows in code, calling LLM per
    parent-sized batch with explicit slice blocks, and normalizing results.
    """
    source_row_lines = row_lines if row_lines is not None else parsed_table.markdown_rows
    if not source_row_lines:
        return []

    # Keep row-summary calls aligned to parent grouping windows (3 child slices).
    rows_per_parent = child_rows_per_chunk * 3
    all_summaries: list[str] = []

    # For each parent-sized batch, pre-slice rows and pass explicit slices to the LLM.
    for batch_start in range(0, len(source_row_lines), rows_per_parent):
        batch_rows = source_row_lines[batch_start : batch_start + rows_per_parent]
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

    expected_total_slices = math.ceil(len(source_row_lines) / child_rows_per_chunk)
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
    child_rows_per_chunk: int,
    file_name: str,
    file_id: str,
) -> tuple[list[ParentChunkModel], list[ChildChunkModel]]:
    headers = (
        classification.col_headers
        if classification.col_headers
        else parsed_table.headers
    )

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
        child_payloads.append(
            {
                "slice_index": slice_index,
                "row_start": row_start + 1,  # 1-based for readability
                "row_end": row_end,
                "rows_markdown": rows_markdown,
                "summary": row_slice_summaries[slice_index],
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
        grouped_row_indices: list[int] = []
        for payload in grouped:
            grouped_row_indices.extend(
                range(int(payload["row_start"]) - 1, int(payload["row_end"]))
            )
        parent_structured_rows = _build_structured_rows(
            parsed_table=parsed_table,
            headers=headers,
            row_indices=grouped_row_indices,
            section_name="",
        )
        parent_density = _section_density_summary(
            [parsed_table.rows[idx] for idx in grouped_row_indices]
        )

        for payload in grouped:
            child_id = generate_uuid_v6()
            parent_child_ids.append(child_id)
            child_row_indices = list(
                range(int(payload["row_start"]) - 1, int(payload["row_end"]))
            )
            child_structured_rows = [
                row
                for row in parent_structured_rows
                if row["row_index"] in child_row_indices
            ]
            child_content = "\n\n".join(
                [
                    f"General Description: {general_description}",
                    f"Row-Specific Description: {payload['summary']}",
                    f"Table:\n{payload['rows_markdown']}",
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
                            "density": _section_density_summary(
                                [parsed_table.rows[idx] for idx in child_row_indices]
                            ),
                            "structured_rows": child_structured_rows,
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
                        "density": parent_density,
                        "structured_rows": parent_structured_rows,
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
            description_and_sections = _build_description_and_sections(
                parsed_table=parsed,
                classification=classification,
                context_before=context_before,
                context_after=context_after,
            )
        except Exception as exc:
            raise TableSemanticIngestionError(
                "Table description and section detection failed "
                f"for block_index={block.block_index}: {exc}"
            ) from exc

        general_description = description_and_sections.description
        section_spans = _section_headers_to_spans(
            parsed_table=parsed,
            section_headers=description_and_sections.sections,
        )
        child_rows_per_chunk = (
            10 if len(classification.col_headers or parsed.headers) <= 10 else 5
        )

        if section_spans:
            table_parents, table_children = _build_section_semantic_chunks_for_table(
                table_id=table_id,
                table_block=block,
                parsed_table=parsed,
                classification=classification,
                general_description=general_description,
                section_spans=section_spans,
                child_rows_per_chunk=child_rows_per_chunk,
                file_name=file_name,
                file_id=file_id,
            )
        else:
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
                child_rows_per_chunk=child_rows_per_chunk,
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
                "section_chunking": bool(section_spans),
                "section_names": [
                    str(section["section_name"]) for section in section_spans
                ],
                "semantic_parent_count": len(table_parents),
                "semantic_child_count": len(table_children),
            }
        )

    _write_semantic_artifact(artifact_dir=artifact_dir, diagnostics=diagnostics)
    return transformed_blocks, semantic_parents, semantic_children, warnings
