"""Shared chunk payload schemas used across ingestion and modification flows.

Centralizing these models avoids coupling Docling/markdown chunkers to the
legacy chunker module path.
"""

from pydantic import BaseModel
from pydantic import Field


class ParentChunkModel(BaseModel):
    """Schema for large context-rich parent chunks stored in the document store."""

    parent_chunk_id: str
    content: str
    file_metadata: dict
    collection_metadata: dict = Field(default_factory=dict)
    parent_chunk_metadata: dict
    content_flags: dict
    artifact_refs: dict


class ChildChunkModel(BaseModel):
    """Schema for small vectorized child chunks stored in the vector store."""

    child_chunk_id: str
    content: str
    file_metadata: dict
    collection_metadata: dict = Field(default_factory=dict)
    child_chunk_metadata: dict
    content_flags: dict
    artifact_refs: dict
