import asyncio
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

fake_vectordb = types.ModuleType("app.vectordb.vectordb")
fake_vectordb.PARENT_STORE = types.SimpleNamespace(collection=None)


async def _unused_async(*args, **kwargs):
    return None


fake_vectordb.delete_children_by_file_id = _unused_async
fake_vectordb.delete_children_by_parent_id = _unused_async
fake_vectordb.delete_parent_documents_by_file_id = _unused_async
fake_vectordb.delete_parent_document = _unused_async
fake_vectordb.upsert_documents = _unused_async
sys.modules["app.vectordb.vectordb"] = fake_vectordb

from app.api import router_modifications


def _build_payload(**overrides):
    payload = {
        "fileId": "file-1",
        "fileName": "Report.txt",
        "selectedText": "old text",
        "startChunkNumber": 1,
        "endChunkNumber": 1,
        "startOffset": 6,
        "endOffset": 14,
        "instruction": "Rewrite the selection",
    }
    payload.update(overrides)
    return router_modifications.SelectionEditPreviewRequest(**payload)


def test_selection_edit_preview_returns_preview(monkeypatch):
    async def _get_file_chunk_window_content(
        file_id: str,
        file_name: str,
        start_chunk_number: int,
        end_chunk_number: int,
    ):
        assert file_id == "file-1"
        assert file_name == "Report.txt"
        assert start_chunk_number == 1
        assert end_chunk_number == 1
        return "Hello old text world", 0

    async def _generate_selection_edit_preview(**kwargs):
        assert kwargs["selected_text"] == "old text"
        return {
            "proposedText": "new text",
        }

    monkeypatch.setattr(
        router_modifications.ReconstructionService,
        "get_file_chunk_window_content",
        _get_file_chunk_window_content,
    )
    monkeypatch.setattr(
        router_modifications.LlmEditorService,
        "generate_selection_edit_preview",
        _generate_selection_edit_preview,
    )

    response = asyncio.run(router_modifications.selection_edit_preview(_build_payload()))
    assert response.fileId == "file-1"
    assert response.selectionId == "selection:6:14"
    assert response.selectedText == "old text"
    assert response.proposedText == "new text"


def test_selection_edit_preview_rejects_empty_instruction():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(router_modifications.selection_edit_preview(_build_payload(instruction="   ")))

    assert exc.value.status_code == 422


def test_selection_edit_preview_rejects_empty_selection():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(router_modifications.selection_edit_preview(_build_payload(selectedText="")))

    assert exc.value.status_code == 422


def test_selection_edit_preview_rejects_invalid_start_chunk_number():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            router_modifications.selection_edit_preview(
                _build_payload(startChunkNumber=0, endChunkNumber=1)
            )
        )

    assert exc.value.status_code == 422


def test_selection_edit_preview_rejects_invalid_chunk_number_range():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            router_modifications.selection_edit_preview(
                _build_payload(startChunkNumber=4, endChunkNumber=3)
            )
        )

    assert exc.value.status_code == 422


def test_selection_edit_preview_rejects_missing_parent(monkeypatch):
    async def _get_file_chunk_window_content(
        _file_id: str,
        _file_name: str,
        _start_chunk_number: int,
        _end_chunk_number: int,
    ):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(
        router_modifications.ReconstructionService,
        "get_file_chunk_window_content",
        _get_file_chunk_window_content,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(router_modifications.selection_edit_preview(_build_payload()))

    assert exc.value.status_code == 404


def test_selection_edit_preview_rejects_mismatched_offsets(monkeypatch):
    async def _get_file_chunk_window_content(
        _file_id: str,
        _file_name: str,
        _start_chunk_number: int,
        _end_chunk_number: int,
    ):
        return "Hello something else world", 0

    monkeypatch.setattr(
        router_modifications.ReconstructionService,
        "get_file_chunk_window_content",
        _get_file_chunk_window_content,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(router_modifications.selection_edit_preview(_build_payload()))

    assert exc.value.status_code == 409


def test_selection_edit_preview_allows_noop(monkeypatch):
    async def _get_file_chunk_window_content(
        _file_id: str,
        _file_name: str,
        _start_chunk_number: int,
        _end_chunk_number: int,
    ):
        return "Hello old text world", 0

    async def _generate_selection_edit_preview(**_kwargs):
        return {
            "proposedText": "old text",
        }

    monkeypatch.setattr(
        router_modifications.ReconstructionService,
        "get_file_chunk_window_content",
        _get_file_chunk_window_content,
    )
    monkeypatch.setattr(
        router_modifications.LlmEditorService,
        "generate_selection_edit_preview",
        _generate_selection_edit_preview,
    )

    response = asyncio.run(router_modifications.selection_edit_preview(_build_payload()))
    assert response.proposedText == "old text"


def test_selection_edit_preview_remaps_rendered_offsets_to_markdown(monkeypatch):
    content = "The speed is **299,792,458 meters** per second."
    selected_plain = "299,792,458 meters per second."
    projected_plain = content.replace("**", "")
    start_offset = projected_plain.index(selected_plain)
    end_offset = start_offset + len(selected_plain)

    async def _get_file_chunk_window_content(
        _file_id: str,
        _file_name: str,
        _start_chunk_number: int,
        _end_chunk_number: int,
    ):
        return content, 0

    async def _generate_selection_edit_preview(**kwargs):
        # Prompt text should remain the user's rendered/plain selection.
        assert kwargs["selected_text"] == selected_plain
        return {
            "proposedText": "299,792 km/s",
        }

    monkeypatch.setattr(
        router_modifications.ReconstructionService,
        "get_file_chunk_window_content",
        _get_file_chunk_window_content,
    )
    monkeypatch.setattr(
        router_modifications.LlmEditorService,
        "generate_selection_edit_preview",
        _generate_selection_edit_preview,
    )

    response = asyncio.run(
        router_modifications.selection_edit_preview(
            _build_payload(
                selectedText=selected_plain,
                startOffset=start_offset,
                endOffset=end_offset,
            )
        )
    )

    # Response is remapped to markdown/file offsets and source substring.
    assert response.startOffset == content.index("**299,792,458 meters")
    assert response.selectedText == "**299,792,458 meters** per second."


def test_selection_edit_preview_returns_absolute_offsets_from_window_prefix(monkeypatch):
    window_content = "chunk one\n\nchunk two"
    window_absolute_start = 100
    selected_text = "chunk two"
    start_offset_local = window_content.index(selected_text)
    end_offset_local = start_offset_local + len(selected_text)

    async def _get_file_chunk_window_content(
        _file_id: str,
        _file_name: str,
        start_chunk_number: int,
        end_chunk_number: int,
    ):
        assert start_chunk_number == 3
        assert end_chunk_number == 4
        return window_content, window_absolute_start

    async def _generate_selection_edit_preview(**_kwargs):
        return {"proposedText": "rewritten"}

    monkeypatch.setattr(
        router_modifications.ReconstructionService,
        "get_file_chunk_window_content",
        _get_file_chunk_window_content,
    )
    monkeypatch.setattr(
        router_modifications.LlmEditorService,
        "generate_selection_edit_preview",
        _generate_selection_edit_preview,
    )

    response = asyncio.run(
        router_modifications.selection_edit_preview(
            _build_payload(
                selectedText=selected_text,
                startChunkNumber=3,
                endChunkNumber=4,
                startOffset=start_offset_local,
                endOffset=end_offset_local,
            )
        )
    )
    assert response.startOffset == window_absolute_start + start_offset_local
    assert response.endOffset == window_absolute_start + end_offset_local


def test_selection_edit_preview_rejects_chunk_window_out_of_range(monkeypatch):
    async def _get_file_chunk_window_content(
        _file_id: str,
        _file_name: str,
        _start_chunk_number: int,
        _end_chunk_number: int,
    ):
        raise ValueError("Requested chunk range does not exist for this file: 4-5")

    monkeypatch.setattr(
        router_modifications.ReconstructionService,
        "get_file_chunk_window_content",
        _get_file_chunk_window_content,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            router_modifications.selection_edit_preview(
                _build_payload(startChunkNumber=4, endChunkNumber=5)
            )
        )

    assert exc.value.status_code == 422


def test_selection_edit_preview_rejects_file_name_conflict(monkeypatch):
    async def _get_file_chunk_window_content(
        _file_id: str,
        _file_name: str,
        _start_chunk_number: int,
        _end_chunk_number: int,
    ):
        raise RuntimeError("file ID 'file-1' belongs to 'Other.txt', not 'Report.txt'")

    monkeypatch.setattr(
        router_modifications.ReconstructionService,
        "get_file_chunk_window_content",
        _get_file_chunk_window_content,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(router_modifications.selection_edit_preview(_build_payload()))

    assert exc.value.status_code == 409


def test_selection_edit_preview_request_requires_chunk_numbers():
    with pytest.raises(ValidationError):
        router_modifications.SelectionEditPreviewRequest(
            fileId="file-1",
            fileName="Report.txt",
            selectedText="old text",
            startOffset=1,
            endOffset=3,
            instruction="Rewrite",
        )
