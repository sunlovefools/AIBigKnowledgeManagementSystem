"""
Shared Pydantic models for Docling extraction.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ExtractedImageArtifact(BaseModel):
    """
    Represents an image extracted from a PDF page, either a regular picture or a table image.
    """

    kind: Literal["picture", "table_image"]
    image_uuid: str
    file_name: str
    file_path: str
    page_no: int | None = None
    table_index: int | None = None
    picture_index: int | None = None
    reason: str | None = None
    s3_bucket: str | None = None
    s3_key: str | None = None
    s3_region: str | None = None
    s3_uri: str | None = None
    s3_upload_status: Literal["uploaded", "failed", "skipped"] | None = None
    s3_error: str | None = None


class DoclingChunkFailure(BaseModel):
    """
    Represents a partial failure in converting a chunk of pages.
    """

    page_range: str
    errors: list[str]


class DoclingParseStats(BaseModel):
    """
    Statistics about the Docling parsing process.
    """

    converted_chunks: int
    partial_failure_chunks: int
    pictures_extracted: int
    table_fallback_images_extracted: int


class DoclingStructuredBlock(BaseModel):
    """
    Normalized Docling markdown block used by structure-aware chunking.
    """

    block_index: int
    block_type: Literal["header", "text", "list", "picture", "table", "other"]
    content: str
    page_no: int | None = None
    is_table_image: bool = False
    table_image_uuid: str | None = None


class DoclingParseResult(BaseModel):
    """
    Represents the result of parsing a PDF with Docling.
    """

    warnings: list[str]
    partial_failures: list[DoclingChunkFailure]
    structured_blocks: list[DoclingStructuredBlock] = Field(default_factory=list)

