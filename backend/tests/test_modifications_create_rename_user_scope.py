import asyncio
import sys
import types
from pathlib import Path

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

fake_s3_image_store = types.ModuleType("app.service.storage.s3_image_store")
fake_s3_image_store.delete_docling_artifacts_by_file_id = lambda _file_id: {
    "s3Status": "skipped",
    "s3DeletedObjects": 0,
    "warnings": [],
}
sys.modules["app.service.storage.s3_image_store"] = fake_s3_image_store

fake_llm_editor_service = types.ModuleType("app.service.modification.llm_editor_service")


class _FakeLlmEditorService:
    @staticmethod
    async def generate_edit_preview(*args, **kwargs):
        return {"editedContent": "", "summary": "", "warnings": []}

    @staticmethod
    async def generate_selection_edit_preview(*args, **kwargs):
        return {"proposedText": ""}


fake_llm_editor_service.LlmEditorService = _FakeLlmEditorService
sys.modules["app.service.modification.llm_editor_service"] = fake_llm_editor_service

from app.api import router_modifications
from app.service.modification import reconstruction_service as rs


class _FakeChunk:
    def __init__(self, data: dict):
        self._data = dict(data)
        self.file_metadata = self._data.get("file_metadata")

    def model_dump(self, by_alias: bool = False):
        return dict(self._data)


def _make_parent_model(*, parent_id: str, content: str, file_id: str, file_name: str) -> _FakeChunk:
    return _FakeChunk(
        {
            "parent_chunk_id": parent_id,
            "content": content,
            "file_metadata": {"file_name": file_name, "file_id": file_id},
            "parent_chunk_metadata": {
                "child_chunks_ids": ["child-1"],
                "parent_chunk_number": 0,
                "page_number": [0],
                "ingested_at": "2026-01-01T00:00:00+00:00",
            },
            "content_flags": {"is_image": False, "is_table_image": False},
            "artifact_refs": {"image_uuid": [], "table_image_uuid": []},
        }
    )


def _make_child_model(*, child_id: str, parent_id: str, content: str, file_id: str, file_name: str) -> _FakeChunk:
    return _FakeChunk(
        {
            "child_chunk_id": child_id,
            "content": content,
            "file_metadata": {"file_name": file_name, "file_id": file_id},
            "child_chunk_metadata": {
                "parent_id": parent_id,
                "child_chunk_number": 0,
                "page_number": 0,
                "has_preamble": False,
                "ingested_at": "2026-01-01T00:00:00+00:00",
            },
            "content_flags": {"is_image": False, "is_table_image": False},
            "artifact_refs": {"image_uuid": None, "table_image_uuid": None},
        }
    )


def test_create_blank_file_endpoint_passes_authenticated_user_id(monkeypatch):
    captured: dict[str, str] = {}

    async def _create_blank_file(*, file_name: str, placeholder_content: str, user_id: str) -> dict:
        captured["file_name"] = file_name
        captured["placeholder_content"] = placeholder_content
        captured["user_id"] = user_id
        return {
            "fileId": "file-1",
            "fileName": file_name,
            "content": placeholder_content,
            "parentId": "parent-1",
            "parentChunks": 1,
            "chunks": 1,
        }

    monkeypatch.setattr(router_modifications.ReconstructionService, "create_blank_file", _create_blank_file)

    payload = router_modifications.CreateBlankFileRequest(fileName="Test")
    response = asyncio.run(
        router_modifications.create_blank_file(payload, current_user={"sub": "user-123"})
    )

    assert captured["file_name"] == "Test"
    assert captured["user_id"] == "user-123"
    assert "# Test" in captured["placeholder_content"]
    assert response.fileId == "file-1"
    assert response.fileName == "Test"


def test_rename_file_endpoint_passes_authenticated_user_id(monkeypatch):
    captured: dict[str, str] = {}

    async def _rename_file(*, file_id: str, new_file_name: str, user_id: str) -> dict:
        captured["file_id"] = file_id
        captured["new_file_name"] = new_file_name
        captured["user_id"] = user_id
        return {
            "fileId": file_id,
            "oldFileName": "Old.md",
            "fileName": new_file_name,
            "parentChunks": 1,
        }

    monkeypatch.setattr(router_modifications.ReconstructionService, "rename_file", _rename_file)

    payload = router_modifications.RenameFileRequest(newFileName="Renamed.md")
    response = asyncio.run(
        router_modifications.rename_file("file-1", payload, current_user={"sub": "user-456"})
    )

    assert captured == {
        "file_id": "file-1",
        "new_file_name": "Renamed.md",
        "user_id": "user-456",
    }
    assert response.fileName == "Renamed.md"


def test_reconstruction_service_create_blank_file_upserts_with_user_id(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr("app.core.id_utils.generate_uuid_v6", lambda: "generated-file-id")

    def _split_markdown(content: str, file_name: str, file_id: str, **_kwargs):
        parent_model = _make_parent_model(
            parent_id="parent-1",
            content=content,
            file_id=file_id,
            file_name=file_name,
        )
        child_model = _make_child_model(
            child_id="child-1",
            parent_id="parent-1",
            content="child content",
            file_id=file_id,
            file_name=file_name,
        )
        return [parent_model], [child_model]

    async def _upsert_documents(*, parent_chunks, child_chunks, user_id: str):
        calls["parent_chunks"] = parent_chunks
        calls["child_chunks"] = child_chunks
        calls["user_id"] = user_id

    monkeypatch.setattr(rs, "split_parent_child_chunks_from_markdown", _split_markdown)
    monkeypatch.setattr(rs, "polish_chunks", lambda chunks: chunks)
    monkeypatch.setattr(rs, "upsert_documents", _upsert_documents)

    result = asyncio.run(
        rs.ReconstructionService.create_blank_file(
            file_name="Test.md",
            placeholder_content="# Test",
            user_id="user-create",
        )
    )

    assert calls["user_id"] == "user-create"
    assert calls["parent_chunks"][0]["file_metadata"]["file_id"] == "generated-file-id"
    assert result["fileId"] == "generated-file-id"
    assert result["parentId"] == "parent-1"


def test_reconstruction_service_rename_file_uses_user_scoped_filter_and_user_scoped_mutations(monkeypatch):
    calls = {
        "name_map_user_id": None,
        "query_filters": [],
        "delete_children": [],
        "delete_parent": [],
        "upsert_user_id": None,
    }

    class _FakeCollection:
        def find(self, query):
            calls["query_filters"].append(query)
            return iter(
                [
                    {
                        "_id": "parent-1",
                        "value": {
                            "page_content": "old content",
                            "metadata": {
                                "file_metadata": {
                                    "file_id": "file-1",
                                    "file_name": "Old.md",
                                },
                                "parent_chunk_metadata": {
                                    "parent_chunk_number": 0,
                                },
                                "user_id": "user-rename",
                            },
                        },
                    }
                ]
            )

    monkeypatch.setattr(rs, "PARENT_STORE", types.SimpleNamespace(collection=_FakeCollection()))

    async def _get_file_names_map(user_id: str) -> dict[str, str]:
        calls["name_map_user_id"] = user_id
        return {}

    async def _delete_children(parent_id: str, user_id: str):
        calls["delete_children"].append((parent_id, user_id))

    async def _delete_parent(parent_id: str, user_id: str):
        calls["delete_parent"].append((parent_id, user_id))

    def _split_markdown(content: str, file_name: str, file_id: str, **_kwargs):
        parent_model = _make_parent_model(
            parent_id="parent-new",
            content=content,
            file_id=file_id,
            file_name=file_name,
        )
        child_model = _make_child_model(
            child_id="child-new",
            parent_id="parent-new",
            content="child content",
            file_id=file_id,
            file_name=file_name,
        )
        return [parent_model], [child_model]

    async def _upsert_documents(*, parent_chunks, child_chunks, user_id: str):
        calls["upsert_user_id"] = user_id

    monkeypatch.setattr(
        rs.ReconstructionService,
        "_get_file_names_map",
        staticmethod(_get_file_names_map),
    )
    monkeypatch.setattr(rs, "delete_children_by_parent_id", _delete_children)
    monkeypatch.setattr(rs, "delete_parent_document", _delete_parent)
    monkeypatch.setattr(rs, "split_parent_child_chunks_from_markdown", _split_markdown)
    monkeypatch.setattr(rs, "polish_chunks", lambda chunks: chunks)
    monkeypatch.setattr(rs, "upsert_documents", _upsert_documents)

    result = asyncio.run(
        rs.ReconstructionService.rename_file(
            file_id="file-1",
            new_file_name="Renamed.md",
            user_id="user-rename",
        )
    )

    assert calls["name_map_user_id"] == "user-rename"
    assert calls["query_filters"][0]["value.metadata.user_id"] == "user-rename"
    assert calls["delete_children"] == [("parent-1", "user-rename")]
    assert calls["delete_parent"] == [("parent-1", "user-rename")]
    assert calls["upsert_user_id"] == "user-rename"
    assert result["fileName"] == "Renamed.md"
