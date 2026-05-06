"""
Element classification helpers for Docling layout processing.
"""

from __future__ import annotations

from typing import Any


def block_type_for_element(
    element: Any,
    *,
    picture_item_cls: Any,
    table_item_cls: Any,
    list_item_cls: Any,
    section_header_item_cls: Any,
    title_item_cls: Any,
) -> str:
    """Classify a Docling element into normalized block categories."""

    if title_item_cls is not None and isinstance(element, title_item_cls):
        return "header"
    if section_header_item_cls is not None and isinstance(element, section_header_item_cls):
        return "header"
    if list_item_cls is not None and isinstance(element, list_item_cls):
        return "list"
    if isinstance(element, picture_item_cls):
        return "picture"
    if isinstance(element, table_item_cls):
        return "table"
    if hasattr(element, "text"):
        return "text"
    return "other"

