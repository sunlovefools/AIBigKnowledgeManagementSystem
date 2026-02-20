"""
API router for file modification and reconstruction operations.
Handles retrieving document lists and reconstructing files from chunks.
"""

import traceback
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from typing import List

from app.service.modification_service import ReconstructionService

# Setup the API router
router = APIRouter()

# --- Data Models ---
class DocumentInfo(BaseModel):
    """Information about a reconstructed document."""
    id: str                   # Parent document ID
    fileName: str             # Original file name
    content: str              # Full reconstructed document content
    size: int                 # Character count
    chunks: int               # Number of chunks this document contains


class ModificationsResponse(BaseModel):
    """Response containing list of available documents for modification."""
    documents: List[DocumentInfo]
    total: int


class FileSummary(BaseModel):
    """Sidebar summary for a merged file item."""
    fileId: str
    fileName: str
    previewTexts: str


class FileSummaryResponse(BaseModel):
    """Response containing merged filenames and their preview snippets."""
    files: List[FileSummary]
    total: int


class ParentChunkContent(BaseModel):
    """Parent chunk content item used by full-view tabs."""
    parentId: str
    content: str
    size: int


class FileChunksResponse(BaseModel):
    """Paginated parent chunks for one merged file item."""
    fileId: str
    chunks: List[ParentChunkContent]
    hasMore: bool
    nextCursor: str | None


class UpdateParentChunkRequest(BaseModel):
    """Payload for updating a parent chunk's reconstructed content."""
    fileName: str
    content: str


class UpdateParentChunkResponse(BaseModel):
    """Response for updated parent chunk content."""
    parentId: str
    previousParentId: str
    fileName: str
    content: str
    size: int
    chunks: int


# --- API Endpoints ---

@router.get("/health")
def modifications_health():
    """Health check endpoint for modifications module."""
    return {"modifications": "ok"}

# The endpoint for retrieving all preview files in a summary format for the left sidebar.
@router.get("/all-preview-files", response_model=FileSummaryResponse)
async def get_all_preview_files():
    """Return filename-merged summaries for the left sidebar."""
    try:
        files = await ReconstructionService.get_all_preview_files()
        response_files = [
            FileSummary(
                fileId=file_item["fileId"],
                fileName=file_item["fileName"],
                previewTexts=file_item["preview"],
            )
            for file_item in files
        ]
        return FileSummaryResponse(files=response_files, total=len(response_files))
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


# The endpoint for retrieving paginated parent chunks for a specific merged file, used by the full-view tabs.
@router.get("/file-chunks", response_model=FileChunksResponse)
async def get_file_chunks(
    file_id: str = Query(..., alias="fileId", min_length=1),
    limit: int = Query(default=7, ge=1, le=20), # This limit is for the number of parent chunks to return per request.
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


@router.get("/document/{document_id}", response_model=DocumentInfo)
async def get_document_content(document_id: str):
    """
    Retrieves the full content of a specific document by ID.
    
    Args:
        document_id (str): The parent document ID
    
    Returns:
        DocumentInfo: The document with its full reconstructed content
    
    Raises:
        HTTPException: If document not found or retrieval fails
    """
    
    try:
        doc = await ReconstructionService.get_document_by_id(document_id)
        
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document with ID '{document_id}' not found"
            )
        
        return DocumentInfo(
            id=document_id,
            fileName="document",  # Not stored for individual retrieval
            content=doc["content"],
            size=doc["size"],
            chunks=0,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve document: {str(e)}"
        )


@router.put("/parent-chunks/{parent_id}", response_model=UpdateParentChunkResponse)
async def update_parent_chunk(parent_id: str, payload: UpdateParentChunkRequest):
    """Update one parent chunk by replacing its content and re-ingesting chunks."""
    try:
        incoming_content = payload.content.strip()
        if not incoming_content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="content must not be empty",
            )

        existing_doc = await ReconstructionService.get_document_by_id(parent_id)
        if not existing_doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document with parent ID '{parent_id}' not found",
            )

        existing_file_name = str(existing_doc.get("fileName") or "Unknown")
        if existing_file_name != payload.fileName:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"parent ID '{parent_id}' belongs to '{existing_file_name}', "
                    f"not '{payload.fileName}'"
                ),
            )

        updated = await ReconstructionService.update_document(
            parent_id=parent_id,
            new_content=payload.content,
            file_name=payload.fileName,
        )

        return UpdateParentChunkResponse(
            parentId=updated["parentId"],
            previousParentId=updated["previousParentId"],
            fileName=updated["fileName"],
            content=updated["content"],
            size=updated["size"],
            chunks=updated["chunks"],
        )
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database service error: {str(e)}",
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update document: {str(e)}",
        )
