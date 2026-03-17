import asyncio
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

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
        "startOffset": 6,
        "endOffset": 14,
        "instruction": "Rewrite the selection",
    }
    payload.update(overrides)
    return router_modifications.SelectionEditPreviewRequest(**payload)


def test_selection_edit_preview_returns_preview(monkeypatch):
    async def _get_file_merged_content(file_id: str, file_name: str):
        assert file_id == "file-1"
        assert file_name == "Report.txt"
        return "Hello old text world"

    async def _generate_selection_edit_preview(**kwargs):
        assert kwargs["selected_text"] == "old text"
        return {
            "proposedText": "new text",
        }

    monkeypatch.setattr(
        router_modifications.ReconstructionService,
        "get_file_merged_content",
        _get_file_merged_content,
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


def test_selection_edit_preview_rejects_missing_parent(monkeypatch):
    async def _get_file_merged_content(_file_id: str, _file_name: str):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(
        router_modifications.ReconstructionService,
        "get_file_merged_content",
        _get_file_merged_content,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(router_modifications.selection_edit_preview(_build_payload()))

    assert exc.value.status_code == 404


def test_selection_edit_preview_rejects_mismatched_offsets(monkeypatch):
    async def _get_file_merged_content(_file_id: str, _file_name: str):
        return "Hello something else world"

    monkeypatch.setattr(
        router_modifications.ReconstructionService,
        "get_file_merged_content",
        _get_file_merged_content,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(router_modifications.selection_edit_preview(_build_payload()))

    assert exc.value.status_code == 409


def test_selection_edit_preview_allows_noop(monkeypatch):
    async def _get_file_merged_content(_file_id: str, _file_name: str):
        return "Hello old text world"

    async def _generate_selection_edit_preview(**_kwargs):
        return {
            "proposedText": "old text",
        }

    monkeypatch.setattr(
        router_modifications.ReconstructionService,
        "get_file_merged_content",
        _get_file_merged_content,
    )
    monkeypatch.setattr(
        router_modifications.LlmEditorService,
        "generate_selection_edit_preview",
        _generate_selection_edit_preview,
    )

    response = asyncio.run(router_modifications.selection_edit_preview(_build_payload()))
    assert response.proposedText == "old text"
