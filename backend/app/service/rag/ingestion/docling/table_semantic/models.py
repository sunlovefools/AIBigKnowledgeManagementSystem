"""
Data models for semantic table ingestion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


TableType = Literal["layout", "matrix", "entity_list"]


@dataclass
class ParsedMarkdownTable:
    headers: list[str]
    rows: list[list[str]]
    markdown_header: str
    markdown_separator: str
    markdown_rows: list[str]


@dataclass
class TableClassification:
    table_type: TableType
    needs_description: bool
    col_headers: list[str] = field(default_factory=list)
    row_headers: list[str] = field(default_factory=list)


@dataclass
class DescriptionAndSections:
    description: str
    sections: list[dict] = field(default_factory=list)


@dataclass
class TableSliceSummary:
    slice_index: int
    summary: str


@dataclass
class SemanticTableBuildResult:
    transformed_block_content: str | None = None
    semantic_parent_chunks: list[dict] = field(default_factory=list)
    semantic_child_chunks: list[dict] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)

