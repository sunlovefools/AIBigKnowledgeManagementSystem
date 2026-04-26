"""Typed MCP contracts for read-only RAG tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SearchScope = Literal["collection", "all_collections"]


class CollectionSummary(BaseModel):
    collectionId: str
    name: str
    isDefault: bool = False
    fileCount: int = 0


class CollectionListResponse(BaseModel):
    collections: list[CollectionSummary] = Field(default_factory=list)
    total: int = 0


class CollectionDescriptor(BaseModel):
    collectionId: str
    name: str
    isDefault: bool = False
    fileCount: int = 0


class FileSummary(BaseModel):
    fileId: str
    fileName: str
    preview: str = ""


class DescribeCollectionResponse(BaseModel):
    collection: CollectionDescriptor
    files: list[FileSummary] = Field(default_factory=list)
    total: int = 0
    truncated: bool = False


class EvidenceItem(BaseModel):
    parentId: str
    fileId: str
    fileName: str
    collectionId: str | None = None
    collectionName: str | None = None
    parentChunkNumber: int | None = None
    snippet: str


class SearchMaterialsResponse(BaseModel):
    query: str
    searchScope: SearchScope
    collection: CollectionDescriptor | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    total: int = 0


class FileSearchResponse(BaseModel):
    query: str
    collection: CollectionDescriptor
    files: list[FileSummary] = Field(default_factory=list)
    total: int = 0
    truncated: bool = False


class ParentChunkContent(BaseModel):
    parentId: str
    fileId: str
    fileName: str
    collectionId: str | None = None
    collectionName: str | None = None
    parentChunkNumber: int | None = None
    content: str
    truncated: bool = False


class FetchParentChunkResponse(BaseModel):
    parentChunk: ParentChunkContent | None = None


class FileOutlineChunk(BaseModel):
    parentId: str
    fileId: str
    fileName: str
    collectionId: str | None = None
    collectionName: str | None = None
    parentChunkNumber: int | None = None
    heading: str | None = None
    preview: str = ""
    size: int = 0


class FileOutlineResponse(BaseModel):
    fileId: str
    collection: CollectionDescriptor
    chunks: list[FileOutlineChunk] = Field(default_factory=list)
    total: int = 0
    truncated: bool = False
