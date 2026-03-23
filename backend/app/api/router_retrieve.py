"""
API router for retrieving reconstructed file and document content.
Handles fetch operations from the backend database.
"""

import traceback
from typing import List

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.service.modification.reconstruction_service import ReconstructionService


router = APIRouter()


class DocumentInfo(BaseModel):
    """Information about a reconstructed document."""
    id: str
    fileName: str
    content: str
    size: int
    chunks: int


class FileSummary(BaseModel):
    """Sidebar summary for a merged file item."""
    fileId: str
    fileName: str
    previewTexts: str


class FileSummaryResponse(BaseModel):
    """Response containing merged filenames and their preview snippets."""
    files: List[FileSummary]
    hasMore: bool
    nextCursor: str | None
    total: int


class ParentChunkContent(BaseModel):
    """Parent chunk content item used by full-view tabs."""
    parentId: str
    content: str
    size: int
    pageNumbers: List[int]


class FileChunksResponse(BaseModel):
    """Paginated parent chunks for one merged file item."""
    fileId: str
    chunks: List[ParentChunkContent]
    hasMore: bool
    nextCursor: str | None


@router.get("/all-preview-files", response_model=FileSummaryResponse)
async def get_all_preview_files(
    limit: int = Query(default=20, ge=1, le=20),
    cursor: str | None = Query(default=None),
):
    """Return filename-merged summaries for the left sidebar."""
    try:
        print(f"Fetching preview files with limit={limit} and cursor={cursor}")
        result = await ReconstructionService.get_all_preview_files(
            limit=limit,
            cursor=cursor,
        )
        response_files = [
            FileSummary(
                fileId=file_item["fileId"],
                fileName=file_item["fileName"],
                previewTexts=file_item["preview"],
            )
            for file_item in result["files"]
        ]
        return FileSummaryResponse(
            files=response_files,
            hasMore=result["hasMore"],
            nextCursor=result["nextCursor"],
            total=result["total"],
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database service error: {str(e)}",
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve file summaries: {str(e)}",
        )


@router.get("/file-chunks", response_model=FileChunksResponse)
async def get_file_chunks(
    file_id: str = Query(..., alias="fileId", min_length=1),
    limit: int = Query(default=7, ge=1, le=20),
    cursor: str | None = Query(default=None),
):
    """Return paginated parent chunks for one merged file ID."""
    try:
        result = await ReconstructionService.get_file_parent_chunks(
            file_id=file_id,
            limit=limit,
            cursor=cursor,
        )

        return FileChunksResponse(
            fileId=result["fileId"],
            chunks=[
                ParentChunkContent(
                    parentId=chunk["parentId"],
                    content=chunk["content"],
                    size=chunk["size"],
                    pageNumbers=chunk.get("pageNumbers", [0]),
                )
                for chunk in result["chunks"]
            ],
            hasMore=result["hasMore"],
            nextCursor=result["nextCursor"],
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database service error: {str(e)}",
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve file chunks: {str(e)}",
        )


# This endpoint is currently not used by the frontend
@router.get("/document/{document_id}", response_model=DocumentInfo)
async def get_document_content(document_id: str):
    """Retrieve the full content of a specific document by parent ID."""
    try:
        doc = await ReconstructionService.get_document_by_id(document_id)

        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document with ID '{document_id}' not found",
            )

        return DocumentInfo(
            id=document_id,
            fileName="document",
            content=doc["content"],
            size=doc["size"],
            chunks=0,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve document: {str(e)}",
        )
