"""
API router for file modification and reconstruction operations.
Handles file update operations.
"""

import traceback
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.service.modification.reconstruction_service import ReconstructionService

# Setup the API router
router = APIRouter()

# --- Data Models ---"""
API router for file modification and reconstruction operations.
Handles file update operations.
"""

import traceback
from fastapi import APIRouter, HTTPException, status, Depends  # ADDED: Depends
from pydantic import BaseModel

from app.service.modification.reconstruction_service import ReconstructionService
from app.core.dependencies import get_current_user  # ADDED: auth dependency

# Setup the API router
router = APIRouter()

# --- Data Models ---
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


class UpdateFileRequest(BaseModel):
    """Payload for updating full merged file content by file ID."""
    fileName: str
    content: str


class UpdateFileResponse(BaseModel):
    """Response for updated full-file content."""
    fileId: str
    previousFileId: str
    fileName: str
    content: str
    size: int
    parentChunks: int
    chunks: int


# --- API Endpoints ---

@router.get("/health")
def modifications_health():
    """Health check endpoint for modifications module."""
    return {"modifications": "ok"}

# Endpoint that is yet to be implemented in the frontend
@router.put("/parent-chunks/{parent_id}", response_model=UpdateParentChunkResponse)
async def update_parent_chunk(
    parent_id: str,
    payload: UpdateParentChunkRequest,
    current_user: dict = Depends(get_current_user),  # ADDED: locks route + provides user_id
):
    """Update one parent chunk by replacing its content and re-ingesting chunks."""
    user_id = current_user["sub"]  # ADDED: used to verify ownership before allowing update

    try:
        incoming_content = payload.content.strip()
        if not incoming_content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="content must not be empty",
            )

        existing_doc = await ReconstructionService.get_document_by_id(
            parent_id,
            user_id=user_id,  # ADDED: ensures the doc belongs to this user before we update it
        )
        if not existing_doc:
            # Returning 404 (not 403) intentionally — don't reveal that the doc exists but belongs to someone else
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
            user_id=user_id,  # ADDED: ReconstructionService must scope the update to this user
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

# Endpoint to update the full merged file content by file ID.
@router.put("/update-file/{file_id}", response_model=UpdateFileResponse)
async def update_file(
    file_id: str,
    payload: UpdateFileRequest,
    current_user: dict = Depends(get_current_user),  # ADDED: locks route + provides user_id
):
    """Update one merged file by replacing full content and re-ingesting chunks."""
    user_id = current_user["sub"]  # ADDED: used to scope the update to this user's files only

    try:
        incoming_content = payload.content.strip()
        if not incoming_content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="content must not be empty",
            )

        updated = await ReconstructionService.update_file(
            file_id=file_id,
            new_content=payload.content,
            file_name=payload.fileName,
            user_id=user_id,  # ADDED: ReconstructionService must scope the update to this user
        )

        return UpdateFileResponse(
            fileId=updated["fileId"],
            previousFileId=updated["previousFileId"],
            fileName=updated["fileName"],
            content=updated["content"],
            size=updated["size"],
            parentChunks=updated["parentChunks"],
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
            detail=f"Failed to update file: {str(e)}",
        )class UpdateParentChunkRequest(BaseModel):
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


class UpdateFileRequest(BaseModel):
    """Payload for updating full merged file content by file ID."""
    fileName: str
    content: str


class UpdateFileResponse(BaseModel):
    """Response for updated full-file content."""
    fileId: str
    previousFileId: str
    fileName: str
    content: str
    size: int
    parentChunks: int
    chunks: int


# --- API Endpoints ---

@router.get("/health")
def modifications_health():
    """Health check endpoint for modifications module."""
    return {"modifications": "ok"}

# Endpoint that is yet to be implemented in the frontend
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

# Endpoint to update the full merged file content by file ID.
@router.put("/update-file/{file_id}", response_model=UpdateFileResponse)
async def update_file(file_id: str, payload: UpdateFileRequest):
    """Update one merged file by replacing full content and re-ingesting chunks."""
    try:
        incoming_content = payload.content.strip()
        if not incoming_content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="content must not be empty",
            )

        updated = await ReconstructionService.update_file(
            file_id=file_id,
            new_content=payload.content,
            file_name=payload.fileName,
        )

        return UpdateFileResponse(
            fileId=updated["fileId"],
            previousFileId=updated["previousFileId"],
            fileName=updated["fileName"],
            content=updated["content"],
            size=updated["size"],
            parentChunks=updated["parentChunks"],
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
            detail=f"Failed to update file: {str(e)}",
        )
