from uuid6 import UUID

from app.service.rag.ingestion.chunk_polisher import polish_chunks
from app.service.rag.ingestion.chunker import split_parent_child_chunks


def test_split_parent_child_chunks_outputs_new_schema():
    text = "This is sentence one. This is sentence two. This is sentence three. " * 40

    parent_chunks, child_chunks = split_parent_child_chunks(
        text=text,
        file_name="sample.txt",
        parent_target_chars=500,
        child_max_chars=120,
        min_parent_chars=200,
        min_child_chars=20,
    )

    assert parent_chunks
    assert child_chunks

    for child in child_chunks:
        payload = child.model_dump()
        assert set(payload.keys()) == {
            "child_chunk_id",
            "content",
            "file_metadata",
            "child_chunk_metadata",
            "content_flags",
            "artifact_refs",
        }

        child_uuid = UUID(payload["child_chunk_id"])
        assert child_uuid.version == 6

        assert payload["file_metadata"]["file_name"] == "sample.txt"
        assert payload["file_metadata"]["file_id"]

        assert payload["child_chunk_metadata"]["parent_id"]
        assert isinstance(payload["child_chunk_metadata"]["child_chunk_number"], int)
        assert isinstance(payload["child_chunk_metadata"]["page_number"], int)
        assert payload["child_chunk_metadata"]["has_preamble"] is False
        assert payload["child_chunk_metadata"]["ingested_at"]
        assert payload["content_flags"] == {"is_image": False, "is_table_image": False}
        assert payload["artifact_refs"] == {"image_uuid": None, "table_image_uuid": None}

    all_child_ids = {child.model_dump()["child_chunk_id"] for child in child_chunks}

    for index, parent in enumerate(parent_chunks):
        payload = parent.model_dump()
        assert set(payload.keys()) == {
            "parent_chunk_id",
            "content",
            "file_metadata",
            "parent_chunk_metadata",
            "content_flags",
            "artifact_refs",
        }

        parent_uuid = UUID(payload["parent_chunk_id"])
        assert parent_uuid.version == 6

        assert payload["file_metadata"]["file_name"] == "sample.txt"
        assert payload["file_metadata"]["file_id"]

        assert payload["parent_chunk_metadata"]["ingested_at"]
        assert payload["parent_chunk_metadata"]["parent_chunk_number"] == index
        assert payload["parent_chunk_metadata"]["page_number"] == [0]
        assert payload["content_flags"] == {"is_image": False, "is_table_image": False}
        assert payload["artifact_refs"] == {"image_uuid": [], "table_image_uuid": []}

        child_ids = payload["parent_chunk_metadata"]["child_chunks_ids"]
        assert child_ids
        assert all(child_id in all_child_ids for child_id in child_ids)


def test_polish_chunks_uses_content_key():
    chunks = [{"content": "  hello\nworld  "}]
    result = polish_chunks(chunks)

    assert result[0]["content"] == "Hello world"
