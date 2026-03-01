"""
Markdown block and marker helpers for Docling pipelines.
"""

from __future__ import annotations

from typing import Any

from app.service.rag.ingestion.docling.config import DOCLING_IMAGE_PLACEHOLDER
from app.service.rag.ingestion.docling.models import DoclingStructuredBlock


def picture_uuid_marker(image_uuid: str) -> str:
    """
    Return markdown comment marker for extracted picture UUIDs.
    """

    return f"<!-- image-uuid: {image_uuid} -->"


def table_image_uuid_marker(image_uuid: str) -> str:
    """
    Return markdown comment marker for fallback table image UUIDs.
    """

    return f"<!-- table-image-uuid: {image_uuid} -->"


def inject_marker_for_picture(serialized_text: str, marker: str) -> str:
    """
    Replace the image placeholder with marker, or append marker if placeholder is absent.
    """

    text = (serialized_text or "").strip()
    if not text:
        return marker
    if DOCLING_IMAGE_PLACEHOLDER in text:
        return text.replace(DOCLING_IMAGE_PLACEHOLDER, marker).strip()
    return f"{text}\n\n{marker}"


def append_markdown_block(
    *,
    markdown_parts: list[str],
    structured_block_metadata: list[dict[str, Any]],
    text: str,
    block_type: str,
    page_no: int | None,
    is_table_image: bool = False,
    table_image_uuid: str | None = None,
) -> None:
    """
    Append markdown text and matching structured metadata in lockstep.
    """

    markdown_parts.append(text)
    structured_block_metadata.append(
        {
            "block_index": len(markdown_parts) - 1,
            "block_type": block_type,
            "page_no": page_no,
            "is_table_image": is_table_image,
            "table_image_uuid": table_image_uuid,
        }
    )


def build_structured_blocks(
    *,
    structured_block_metadata: list[dict[str, Any]],
    markdown_parts: list[str],
) -> list[DoclingStructuredBlock]:
    """
    Materialize structured blocks from block metadata and generated markdown parts.
    """

    return [
        DoclingStructuredBlock(
            block_index=metadata["block_index"],
            block_type=metadata["block_type"],
            content=markdown_parts[metadata["block_index"]],
            page_no=metadata["page_no"],
            is_table_image=metadata["is_table_image"],
            table_image_uuid=metadata["table_image_uuid"],
        )
        for metadata in structured_block_metadata
        if metadata["block_index"] < len(markdown_parts)
    ]

