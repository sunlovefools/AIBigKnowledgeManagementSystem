"""
PDF utilities for Docling ingestion.
"""

from __future__ import annotations

from typing import Any

import fitz

from app.service.rag.ingestion.docling.config import BEAM_DOCLING_CLIENT_CROP_SCALE


def extract_page_no(doc_item: Any) -> int | None:
    """
    Extract the page number from a Docling document item, if available.
    """

    prov = getattr(doc_item, "prov", None) or []
    if not prov:
        return None
    return getattr(prov[0], "page_no", None)


def coerce_endpoint_table_shape(
    endpoint_item: dict[str, Any],
) -> tuple[int | None, int | None]:
    """
    Extract table row/column counts from endpoint metadata when present.
    """

    table_info = endpoint_item.get("table_info")
    if not isinstance(table_info, dict):
        return None, None
    num_rows = table_info.get("num_rows")
    num_cols = table_info.get("num_cols")
    return (
        num_rows if isinstance(num_rows, int) else None,
        num_cols if isinstance(num_cols, int) else None,
    )


def crop_image_bytes_from_endpoint_item(
    endpoint_item: dict[str, Any],
    pdf_doc: fitz.Document,
    *,
    scale: float = BEAM_DOCLING_CLIENT_CROP_SCALE,
) -> bytes | None:
    """
    Crop an image region from source PDF using endpoint-provided page_no + bbox.
    """

    if not isinstance(endpoint_item, dict):
        return None

    bbox = endpoint_item.get("bbox")
    page_no = endpoint_item.get("page_no")
    if not isinstance(bbox, dict) or not isinstance(page_no, int):
        return None
    if page_no <= 0 or page_no > len(pdf_doc):
        return None

    try:
        left_raw = float(bbox["l"])
        top_raw = float(bbox["t"])
        right_raw = float(bbox["r"])
        bottom_raw = float(bbox["b"])
    except Exception:
        return None

    page = pdf_doc.load_page(page_no - 1)
    page_rect = page.rect
    coord_origin = str(bbox.get("coord_origin", "TOPLEFT")).upper()

    if coord_origin == "BOTTOMLEFT":
        y1 = page_rect.height - top_raw
        y2 = page_rect.height - bottom_raw
    else:
        y1 = top_raw
        y2 = bottom_raw

    clip = fitz.Rect(
        min(left_raw, right_raw),
        min(y1, y2),
        max(left_raw, right_raw),
        max(y1, y2),
    )
    clip = clip & page_rect
    if clip.is_empty or clip.width <= 0 or clip.height <= 0:
        return None

    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    if pix.width <= 0 or pix.height <= 0:
        return None
    return pix.tobytes("png")

