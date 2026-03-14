"""
API router for file modification and reconstruction operations.
Handles file update operations.
"""

import traceback
from typing import Literal
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.service.modification.reconstruction_service import ReconstructionService
from app.service.modification.llm_editor_service import LlmEditorService

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


class DeleteFileResponse(BaseModel):
    """Response for deleting one merged file and its sidecar artifacts."""
    fileId: str
    fileName: str
    deletedParentChunks: int
    deletedChildChunks: int
    s3Status: Literal["deleted", "not_found", "skipped", "failed"]
    s3DeletedObjects: int
    warnings: list[str] = []


class LlmEditPreviewRequest(BaseModel):
    """Payload for requesting an LLM-driven edit preview."""
    fileName: str
    originalContent: str
    instruction: str


class LlmEditPreviewResponse(BaseModel):
    """Response payload for LLM edit preview."""
    editedContent: str
    summary: str
    warnings: list[str] = []


# --- API Endpoints ---

@router.get("/health")
def modifications_health():
    """Health check endpoint for modifications module."""
    return {"modifications": "ok"}


@router.post("/llm-edit-preview", response_model=LlmEditPreviewResponse)
async def llm_edit_preview(payload: LlmEditPreviewRequest):
    """Generate a non-persistent edit preview from natural-language instruction."""
    try:
        file_name = payload.fileName.strip()
        original_content = payload.originalContent
        instruction = payload.instruction.strip()

        if not file_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="fileName must not be empty",
            )

        if not original_content.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="originalContent must not be empty",
            )

        if not instruction:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="instruction must not be empty",
            )

        preview = await LlmEditorService.generate_edit_preview(
            file_name=file_name,
            original_content=original_content,
            instruction=instruction,
        )

        return LlmEditPreviewResponse(
            editedContent=preview["editedContent"],
            summary=preview["summary"],
            warnings=preview.get("warnings", []),
        )
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM service error: {str(e)}",
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate LLM edit preview: {str(e)}",
        )

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

# Endpoint to delete one merged file by file ID from vector database and best-effort S3.
@router.delete("/files/{file_id}", response_model=DeleteFileResponse)
async def delete_file_by_id(file_id: str):
    """Delete one merged file by file ID from Astra and best-effort S3."""
    try:
        deleted = await ReconstructionService.delete_file(file_id=file_id)
        return DeleteFileResponse(
            fileId=deleted["fileId"],
            fileName=deleted["fileName"],
            deletedParentChunks=deleted["deletedParentChunks"],
            deletedChildChunks=deleted["deletedChildChunks"],
            s3Status=deleted["s3Status"],
            s3DeletedObjects=deleted["s3DeletedObjects"],
            warnings=deleted["warnings"],
        )
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database service error: {str(error)}",
        )
    except Exception as error:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete file: {str(error)}",
        )
