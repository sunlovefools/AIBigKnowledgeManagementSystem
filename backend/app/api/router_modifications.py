"""
API router for file modification and reconstruction operations.
Handles file update operations.
"""

import asyncio
import json
import re
import traceback
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.dependencies import get_current_user
from app.service.modification.reconstruction_service import ReconstructionService
from app.service.modification.llm_editor_service import LlmEditorService

# Setup the API router
router = APIRouter()
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]

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
    startChunkNumber: int
    endChunkNumber: int
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


def _build_markdown_plain_projection(markdown: str) -> tuple[str, list[int]]:
    """
    Project markdown into approximately rendered plain text while keeping a map
    from each projected character to its original markdown index.
    """
    plain_chars: list[str] = []
    plain_to_markdown: list[int] = []
    index = 0
    line_start = True
    in_fence = False

    while index < len(markdown):
        if line_start:
            # Strip common line-level markdown markers from projected text.
            if markdown.startswith("```", index):
                newline_index = markdown.find("\n", index)
                if newline_index == -1:
                    break
                in_fence = not in_fence
                plain_chars.append("\n")
                plain_to_markdown.append(newline_index)
                index = newline_index + 1
                line_start = True
                continue

            if not in_fence:
                leading_space_match = re.match(r"[ ]{0,3}", markdown[index:])
                leading_spaces = leading_space_match.group(0) if leading_space_match else ""
                line_marker_start = index + len(leading_spaces)
                marker_consumed = 0

                if line_marker_start < len(markdown) and markdown[line_marker_start] == ">":
                    marker_consumed = 1
                    if (
                        line_marker_start + marker_consumed < len(markdown)
                        and markdown[line_marker_start + marker_consumed] == " "
                    ):
                        marker_consumed += 1
                else:
                    heading_match = re.match(r"#{1,6}[ \t]+", markdown[line_marker_start:])
                    unordered_list_match = re.match(r"[-*+][ \t]+", markdown[line_marker_start:])
                    ordered_list_match = re.match(r"\d+[.)][ \t]+", markdown[line_marker_start:])
                    if heading_match:
                        marker_consumed = len(heading_match.group(0))
                    elif unordered_list_match:
                        marker_consumed = len(unordered_list_match.group(0))
                    elif ordered_list_match:
                        marker_consumed = len(ordered_list_match.group(0))

                if marker_consumed > 0:
                    index = line_marker_start + marker_consumed
                    line_start = False
                    continue

        current = markdown[index]

        # Strip inline markdown markers.
        if not in_fence and markdown.startswith(("**", "__", "~~"), index):
            index += 2
            continue
        if not in_fence and current in ("*", "_", "`"):
            index += 1
            continue

        # Preserve escaped characters but drop the escape slash.
        if current == "\\" and index + 1 < len(markdown):
            escaped_index = index + 1
            plain_chars.append(markdown[escaped_index])
            plain_to_markdown.append(escaped_index)
            line_start = markdown[escaped_index] == "\n"
            index += 2
            continue

        plain_chars.append(current)
        plain_to_markdown.append(index)
        line_start = current == "\n"
        index += 1

    return "".join(plain_chars), plain_to_markdown


def _map_plain_range_to_markdown_range(
    plain_to_markdown: list[int],
    markdown_length: int,
    start_offset: int,
    end_offset: int,
) -> tuple[int, int] | None:
    if start_offset < 0 or end_offset <= start_offset:
        return None
    if not plain_to_markdown:
        return None
    if start_offset >= len(plain_to_markdown):
        return None
    if end_offset > len(plain_to_markdown):
        return None

    markdown_start = plain_to_markdown[start_offset]
    if end_offset == len(plain_to_markdown):
        markdown_end = markdown_length
    else:
        markdown_end = plain_to_markdown[end_offset - 1] + 1

    if markdown_end <= markdown_start:
        return None

    return markdown_start, markdown_end


def _find_nearest_occurrence(haystack: str, needle: str, expected_offset: int) -> int:
    if not needle:
        return -1
    first = haystack.find(needle)
    if first == -1:
        return -1

    best = first
    best_distance = abs(first - expected_offset)
    cursor = first
    while cursor != -1:
        next_index = haystack.find(needle, cursor + 1)
        if next_index == -1:
            break
        distance = abs(next_index - expected_offset)
        if distance < best_distance:
            best = next_index
            best_distance = distance
        cursor = next_index
    return best


def _expand_selection_to_balanced_markers(
    markdown: str,
    start_offset: int,
    end_offset: int,
) -> tuple[int, int]:
    """
    Expand the mapped range to include adjacent markdown style markers when the
    range crosses out of a formatted span. This prevents dangling markers after
    replacement.
    """
    start = start_offset
    end = end_offset
    markers = ("**", "__", "~~", "*", "_", "`")

    for marker in markers:
        marker_len = len(marker)
        if start >= marker_len and markdown[start - marker_len:start] == marker:
            closing_index = markdown.find(marker, start)
            if closing_index != -1 and closing_index < end:
                start -= marker_len

        if end + marker_len <= len(markdown) and markdown[end:end + marker_len] == marker:
            opening_index = markdown.rfind(marker, start, end)
            if opening_index != -1:
                end += marker_len

    return start, end


def _resolve_selection_offsets(
    existing_content: str,
    selected_text_from_view: str,
    start_offset_from_view: int,
    end_offset_from_view: int,
) -> tuple[int, int, str] | None:
    """
    Resolve the markdown offsets for a user's text selection in the document view.
    """

    # Fast path: caller already provided markdown-accurate offsets and text. Directly compare
    if (
        0 <= start_offset_from_view < end_offset_from_view <= len(existing_content)
        and existing_content[start_offset_from_view:end_offset_from_view] == selected_text_from_view
    ):
        return start_offset_from_view, end_offset_from_view, selected_text_from_view

    projected_plain, plain_to_markdown = _build_markdown_plain_projection(existing_content)
    if not projected_plain or not plain_to_markdown:
        return None

    # First try direct plain-text offsets from rendered document.
    if (
        0 <= start_offset_from_view < end_offset_from_view <= len(projected_plain)
        and projected_plain[start_offset_from_view:end_offset_from_view] == selected_text_from_view
    ):
        mapped = _map_plain_range_to_markdown_range(
            plain_to_markdown=plain_to_markdown,
            markdown_length=len(existing_content),
            start_offset=start_offset_from_view,
            end_offset=end_offset_from_view,
        )
        if mapped:
            mapped_start, mapped_end = mapped
            mapped_start, mapped_end = _expand_selection_to_balanced_markers(
                markdown=existing_content,
                start_offset=mapped_start,
                end_offset=mapped_end,
            )
            return mapped_start, mapped_end, existing_content[mapped_start:mapped_end]

    # Fallback: locate the selected plain text nearest the expected offset.
    nearest_start = _find_nearest_occurrence(
        haystack=projected_plain,
        needle=selected_text_from_view,
        expected_offset=max(0, start_offset_from_view),
    )
    if nearest_start == -1:
        return None

    nearest_end = nearest_start + len(selected_text_from_view)
    mapped = _map_plain_range_to_markdown_range(
        plain_to_markdown=plain_to_markdown,
        markdown_length=len(existing_content),
        start_offset=nearest_start,
        end_offset=nearest_end,
    )
    if not mapped:
        return None

    mapped_start, mapped_end = mapped
    mapped_start, mapped_end = _expand_selection_to_balanced_markers(
        markdown=existing_content,
        start_offset=mapped_start,
        end_offset=mapped_end,
    )
    return mapped_start, mapped_end, existing_content[mapped_start:mapped_end]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_progress_event(
    *,
    stage: str,
    status_value: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Shared event contract for selection-preview stream progress updates.
    event: dict[str, Any] = {
        "stage": stage,
        "status": status_value,
        "message": message,
        "timestamp": _now_iso(),
    }
    if isinstance(metadata, dict) and metadata:
        event["metadata"] = metadata
    return event


def _format_sse(event_name: str, data: dict[str, Any]) -> str:
    # Format one server-sent event frame.
    return f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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


async def _selection_edit_preview_core(
    payload: SelectionEditPreviewRequest,
    user_id: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> SelectionEditPreviewResponse:
    print(
        f"[Selection Edit Preview] Received request for modification with instruction: "
        f"'{payload.instruction}' on fileId: '{payload.fileId}'"
    )

    file_id = payload.fileId.strip()
    file_name = payload.fileName.strip()
    selected_text = payload.selectedText
    instruction = payload.instruction.strip()
    start_offset = payload.startOffset
    end_offset = payload.endOffset
    start_chunk_number = payload.startChunkNumber
    end_chunk_number = payload.endChunkNumber

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
    if start_chunk_number < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="startChunkNumber must be >= 1",
        )
    if end_chunk_number < start_chunk_number:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="endChunkNumber must be >= startChunkNumber",
        )

    if progress_callback:
        # Top-level lifecycle event for selection edit preview.
        await progress_callback(
            _build_progress_event(
                stage="selection_edit_preview",
                status_value="started",
                message="Selection edit preview started.",
                metadata={"fileId": file_id},
            )
        )

    if progress_callback:
        # Explicit start/completion signals for chunk window resolution.
        await progress_callback(
            _build_progress_event(
                stage="selection_window_resolution",
                status_value="started",
                message="Resolving selected chunk window.",
            )
        )
    try:
        # Resolve only the requested chunk window, not the whole file.
        try:
            window_content, window_absolute_start = await ReconstructionService.get_file_chunk_window_content(
                file_id=file_id,
                file_name=file_name,
                start_chunk_number=start_chunk_number,
                end_chunk_number=end_chunk_number,
                user_id=user_id,
            )
        except TypeError:
            window_content, window_absolute_start = await ReconstructionService.get_file_chunk_window_content(
                file_id=file_id,
                file_name=file_name,
                start_chunk_number=start_chunk_number,
                end_chunk_number=end_chunk_number,
            )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No parent chunks found for file ID '{file_id}'",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except RuntimeError as e:
        detail = str(e)
        if "belongs to" in detail:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=detail,
            )
        raise

    if progress_callback:
        await progress_callback(
            _build_progress_event(
                stage="selection_window_resolution",
                status_value="completed",
                message="Selection chunk window resolved.",
            )
        )

    # Resolve range-local offsets against the selected chunk window content.
    if progress_callback:
        # Map UI offsets to canonical markdown offsets before patch generation.
        await progress_callback(
            _build_progress_event(
                stage="selection_offset_mapping",
                status_value="started",
                message="Mapping selected text to latest markdown offsets.",
            )
        )
    resolved_selection = _resolve_selection_offsets(
        existing_content=window_content,
        selected_text_from_view=selected_text,
        start_offset_from_view=start_offset,
        end_offset_from_view=end_offset,
    )
    if not resolved_selection:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="selectedText does not match the current content at the provided offsets",
        )
    resolved_start_offset_local, resolved_end_offset_local, selected_text_for_patch = resolved_selection
    resolved_start_offset = window_absolute_start + resolved_start_offset_local
    resolved_end_offset = window_absolute_start + resolved_end_offset_local
    if progress_callback:
        await progress_callback(
            _build_progress_event(
                stage="selection_offset_mapping",
                status_value="completed",
                message="Selection offsets resolved against latest content.",
            )
        )

    # Generate the edit result from LLM based on the selection and instruction.
    if progress_callback:
        # Separate LLM stage so UI can reflect model-generation latency specifically.
        await progress_callback(
            _build_progress_event(
                stage="selection_llm_generation",
                status_value="started",
                message="Generating rewritten text from the LLM.",
            )
        )
    preview = await LlmEditorService.generate_selection_edit_preview(
        file_name=file_name,
        selected_text=selected_text,
        instruction=instruction,
    )
    if progress_callback:
        await progress_callback(
            _build_progress_event(
                stage="selection_llm_generation",
                status_value="completed",
                message="LLM rewrite generated.",
            )
        )

    response = SelectionEditPreviewResponse(
        fileId=file_id,
        fileName=file_name,
        selectionId=f"selection:{resolved_start_offset}:{resolved_end_offset}",
        selectedText=selected_text_for_patch,
        proposedText=preview["proposedText"],
        startOffset=resolved_start_offset,
        endOffset=resolved_end_offset,
    )
    if progress_callback:
        await progress_callback(
            _build_progress_event(
                stage="selection_edit_preview",
                status_value="completed",
                message="Selection edit preview completed.",
            )
        )
    return response


@router.post("/selection-edit-preview", response_model=SelectionEditPreviewResponse)
async def selection_edit_preview(
    payload: SelectionEditPreviewRequest,
    current_user: dict = Depends(get_current_user),
):
    """Generate a non-persistent edit preview for a highlighted text selection."""
    user_id = str(current_user.get("sub") or "").strip()
    try:
        return await _selection_edit_preview_core(payload, user_id=user_id)
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


@router.post("/selection-edit-preview-stream")
async def selection_edit_preview_stream(
    payload: SelectionEditPreviewRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user.get("sub") or "").strip()
    # Queue allows asynchronous production of progress and final result frames.
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _push_progress(event_payload: dict[str, Any]) -> None:
        await queue.put(_format_sse("progress", event_payload))

    async def _runner() -> None:
        try:
            response = await _selection_edit_preview_core(
                payload,
                user_id=user_id,
                progress_callback=_push_progress,
            )
            # Emit normalized API response as the terminal result event.
            await queue.put(_format_sse("result", response.model_dump()))
        except HTTPException as exc:
            await queue.put(
                _format_sse(
                    "error",
                    {
                        "statusCode": int(exc.status_code),
                        "detail": str(exc.detail),
                    },
                )
            )
        except RuntimeError as exc:
            await queue.put(
                _format_sse(
                    "error",
                    {
                        "statusCode": status.HTTP_503_SERVICE_UNAVAILABLE,
                        "detail": f"LLM service error: {str(exc)}",
                    },
                )
            )
        except Exception as exc:
            traceback.print_exc()
            await queue.put(
                _format_sse(
                    "error",
                    {
                        "statusCode": status.HTTP_500_INTERNAL_SERVER_ERROR,
                        "detail": f"Failed to generate selection edit preview: {str(exc)}",
                    },
                )
            )
        finally:
            # Sentinel value to close event-stream generator.
            await queue.put(None)

    task = asyncio.create_task(_runner())

    async def _event_stream():
        try:
            while True:
                next_item = await queue.get()
                if next_item is None:
                    break
                yield next_item
        finally:
            # Stop background work if the client disconnects mid-stream.
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

@router.post("/parent-chunks/batch-update", response_model=BatchUpdateParentChunksResponse)
async def batch_update_parent_chunks(
    payload: BatchUpdateParentChunksRequest,
    current_user: dict = Depends(get_current_user),
):
    """Update multiple parent chunks under one file scope."""
    user_id = str(current_user.get("sub") or "").strip()
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

        try:
            result = await ReconstructionService.update_parent_chunks_batch(
                file_id=file_id,
                file_name=file_name,
                updates=updates,
                user_id=user_id,
                mode=mode,
                full_content=full_content,
                touched_parent_ids=touched_parent_ids,
            )
        except TypeError:
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
async def update_file(
    file_id: str,
    payload: UpdateFileRequest,
    current_user: dict = Depends(get_current_user),
):
    """Update one merged file by replacing full content and re-ingesting chunks."""
    user_id = str(current_user.get("sub") or "").strip()
    try:
        incoming_content = payload.content.strip()
        if not incoming_content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="content must not be empty",
            )

        try:
            updated = await ReconstructionService.update_file(
                file_id=file_id,
                new_content=payload.content,
                file_name=payload.fileName,
                user_id=user_id,
            )
        except TypeError:
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
async def delete_file_by_id(
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete one merged file by file ID from Astra and best-effort S3."""
    user_id = str(current_user.get("sub") or "").strip()
    try:
        try:
            deleted = await ReconstructionService.delete_file(file_id=file_id, user_id=user_id)
        except TypeError:
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
