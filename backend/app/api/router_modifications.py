"""
API router for file modification and reconstruction operations.
Handles file update operations.
"""

import traceback
import logging
from typing import Literal
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.service.modification.reconstruction_service import ReconstructionService
from app.service.modification.llm_editor_service import LlmEditorService
from app.service.modification.multi_file_editor_service import FileCandidate, MultiFileEditorService

# Setup the API router
router = APIRouter()
logger = logging.getLogger("uvicorn.error")

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


class BatchTarget(BaseModel):
    """One target file payload for batch edit preview."""
    fileName: str
    originalContent: str


class AutoSelectOptions(BaseModel):
    """Options for auto file selection strategy."""
    maxFiles: int = 999999
    minScore: float = 0.0


class LlmEditPreviewBatchRequest(BaseModel):
    """Payload for batch edit preview generation."""
    instruction: str
    selectionMode: Literal["manual", "auto"] = "auto"
    targets: list[BatchTarget] = []
    autoCandidates: list[BatchTarget] = []
    activeFileName: str | None = None
    autoSelectOptions: AutoSelectOptions = AutoSelectOptions()


class SelectedFileInfo(BaseModel):
    """Selected file and explainability metadata."""
    fileName: str
    score: float | None = None
    reasons: list[str] = []


class BatchPreviewResultItem(BaseModel):
    """Per-file result in batch edit preview response."""
    fileName: str
    ok: bool
    editedContent: str | None = None
    summary: str | None = None
    warnings: list[str] = []
    error: str | None = None


class BatchPreviewStats(BaseModel):
    """Statistics for a batch edit preview request."""
    total: int
    success: int
    failed: int


class LlmEditPreviewBatchResponse(BaseModel):
    """Response payload for batch edit preview."""
    selectionMode: Literal["manual", "auto"]
    selectedFiles: list[SelectedFileInfo]
    results: list[BatchPreviewResultItem]
    stats: BatchPreviewStats


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


@router.post("/llm-edit-preview-batch", response_model=LlmEditPreviewBatchResponse)
async def llm_edit_preview_batch(payload: LlmEditPreviewBatchRequest):
    """Generate multi-file non-persistent edit previews from one instruction."""
    try:
        instruction = payload.instruction.strip()
        if not instruction:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="instruction must not be empty",
            )

        selection_mode = payload.selectionMode
        logger.info(
            "[AI-BATCH] Request received: mode=%s, manual_targets=%d, auto_candidates=%d",
            selection_mode,
            len(payload.targets),
            len(payload.autoCandidates),
        )

        targets: list[FileCandidate] = []
        for target in payload.targets:
            file_name = target.fileName.strip()
            content = target.originalContent
            if not file_name or not content.strip():
                continue
            targets.append(FileCandidate(file_name=file_name, original_content=content))

        auto_candidates: list[FileCandidate] = []
        for candidate in payload.autoCandidates:
            file_name = candidate.fileName.strip()
            content = candidate.originalContent
            if not file_name or not content.strip():
                continue
            auto_candidates.append(FileCandidate(file_name=file_name, original_content=content))

        if selection_mode == "manual" and not targets:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="targets must not be empty in manual mode",
            )

        max_files = max(1, payload.autoSelectOptions.maxFiles)
        min_score = max(0.0, min(payload.autoSelectOptions.minScore, 1.0))

        batch_result = await MultiFileEditorService.generate_batch_edit_preview(
            instruction=instruction,
            selection_mode=selection_mode,
            targets=targets,
            auto_candidates=auto_candidates,
            max_files=max_files,
            min_score=min_score,
            active_file_name=payload.activeFileName.strip() if payload.activeFileName else None,
        )

        logger.info(
            "[AI-BATCH] Response ready: selected=%d, total=%d, success=%d, failed=%d",
            len(batch_result["selectedFiles"]),
            batch_result["stats"]["total"],
            batch_result["stats"]["success"],
            batch_result["stats"]["failed"],
        )

        return LlmEditPreviewBatchResponse(
            selectionMode=batch_result["selectionMode"],
            selectedFiles=[
                SelectedFileInfo(
                    fileName=item["fileName"],
                    score=item.get("score"),
                    reasons=item.get("reasons", []),
                )
                for item in batch_result["selectedFiles"]
            ],
            results=[
                BatchPreviewResultItem(
                    fileName=item["fileName"],
                    ok=item["ok"],
                    editedContent=item.get("editedContent"),
                    summary=item.get("summary"),
                    warnings=item.get("warnings", []),
                    error=item.get("error"),
                )
                for item in batch_result["results"]
            ],
            stats=BatchPreviewStats(
                total=batch_result["stats"]["total"],
                success=batch_result["stats"]["success"],
                failed=batch_result["stats"]["failed"],
            ),
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
            detail=f"Failed to generate LLM batch edit preview: {str(e)}",
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
