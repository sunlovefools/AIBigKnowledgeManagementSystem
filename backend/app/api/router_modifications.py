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
class BatchUpdateParentChunkItem(BaseModel):
    """One parent chunk update payload in a batch request."""
    parentId: str
    content: str


class BatchUpdateParentChunksRequest(BaseModel):
    """Batch payload for updating multiple parent chunks in one file scope."""
    fileId: str
    fileName: str
    mode: Literal["fast_updates", "boundary_rechunk"] = "fast_updates"
    updates: list[BatchUpdateParentChunkItem] | None = None
    fullContent: str | None = None
    touchedParentIds: list[str] | None = None


class UpdateParentChunkResponse(BaseModel):
    """Response for updated parent chunk content."""
    parentId: str
    previousParentId: str
    fileName: str
    content: str
    size: int
    chunks: int


class BatchUpdateParentChunksResponse(BaseModel):
    """Response for batch parent chunk updates."""
    fileId: str
    fileName: str
    updatedCount: int
    results: list[UpdateParentChunkResponse]
    requiresReload: bool = False


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


class SelectionEditPreviewRequest(BaseModel):
    """Payload for requesting an LLM-driven edit preview for highlighted text."""
    fileId: str
    fileName: str
    selectedText: str
    startOffset: int
    endOffset: int
    instruction: str


class SelectionEditPreviewResponse(BaseModel):
    """Response payload for selected-text edit preview."""
    fileId: str
    fileName: str
    selectionId: str
    selectedText: str
    proposedText: str
    startOffset: int
    endOffset: int


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


@router.post("/selection-edit-preview", response_model=SelectionEditPreviewResponse)
async def selection_edit_preview(payload: SelectionEditPreviewRequest):
    """Generate a non-persistent edit preview for a highlighted text selection."""

    print(
        f"[Selection Edit Preview] Received request for modification with instruction: "
        f"'{payload.instruction}' on fileId: '{payload.fileId}'"
    )

    try:
        file_id = payload.fileId.strip()
        file_name = payload.fileName.strip()
        selected_text = payload.selectedText
        instruction = payload.instruction.strip()
        start_offset = payload.startOffset
        end_offset = payload.endOffset

        if not file_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="fileId must not be empty",
            )

        if not file_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="fileName must not be empty",
            )

        if not selected_text:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="selectedText must not be empty",
            )

        if not instruction:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="instruction must not be empty",
            )

        if start_offset < 0 or end_offset <= start_offset:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="startOffset/endOffset must describe a non-empty range",
            )

        try:
            existing_content = await ReconstructionService.get_file_merged_content(
                file_id=file_id,
                file_name=file_name,
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No parent chunks found for file ID '{file_id}'",
            )
        except RuntimeError as e:
            detail = str(e)
            if "belongs to" in detail:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=detail,
                )
            raise

        if end_offset > len(existing_content):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Selection offsets are out of range for the current file content",
            )

        if existing_content[start_offset:end_offset] != selected_text:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="selectedText does not match the current content at the provided offsets",
            )

        preview = await LlmEditorService.generate_selection_edit_preview(
            file_name=file_name,
            selected_text=selected_text,
            instruction=instruction,
        )

        return SelectionEditPreviewResponse(
            fileId=file_id,
            fileName=file_name,
            selectionId=f"selection:{start_offset}:{end_offset}",
            selectedText=selected_text,
            proposedText=preview["proposedText"],
            startOffset=start_offset,
            endOffset=end_offset,
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
            detail=f"Failed to generate selection edit preview: {str(e)}",
        )

@router.post("/parent-chunks/batch-update", response_model=BatchUpdateParentChunksResponse)
async def batch_update_parent_chunks(payload: BatchUpdateParentChunksRequest):
    """Update multiple parent chunks under one file scope."""
    try:
        file_id = payload.fileId.strip()
        file_name = payload.fileName.strip()
        mode = payload.mode

        if not file_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="fileId must not be empty",
            )

        if not file_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="fileName must not be empty",
            )

        updates: list[dict[str, str]] = []
        full_content: str | None = None
        touched_parent_ids: list[str] = []

        if mode == "fast_updates":
            if not payload.updates:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="updates must contain at least one parent chunk update when mode='fast_updates'",
                )
            updates = [
                {"parentId": item.parentId, "content": item.content}
                for item in payload.updates
            ]
        elif mode == "boundary_rechunk":
            full_content = str(payload.fullContent or "")
            touched_parent_ids = [str(parent_id) for parent_id in (payload.touchedParentIds or [])]
            if not full_content.strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="fullContent must not be empty when mode='boundary_rechunk'",
                )
            if not touched_parent_ids:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="touchedParentIds must contain at least one parent ID when mode='boundary_rechunk'",
                )

        result = await ReconstructionService.update_parent_chunks_batch(
            file_id=file_id,
            file_name=file_name,
            updates=updates,
            mode=mode,
            full_content=full_content,
            touched_parent_ids=touched_parent_ids,
        )

        return BatchUpdateParentChunksResponse(
            fileId=result["fileId"],
            fileName=result["fileName"],
            updatedCount=result["updatedCount"],
            results=[
                UpdateParentChunkResponse(
                    parentId=item["parentId"],
                    previousParentId=item["previousParentId"],
                    fileName=item["fileName"],
                    content=item["content"],
                    size=item["size"],
                    chunks=item["chunks"],
                )
                for item in result["results"]
            ],
            requiresReload=bool(result.get("requiresReload", False)),
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
            detail=f"Failed to batch update parent chunks: {str(e)}",
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
