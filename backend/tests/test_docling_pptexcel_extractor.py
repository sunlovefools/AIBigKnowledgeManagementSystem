import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.ingestion import docling_pptexcel_extractor as extractor
from app.service.rag.ingestion.docling.models import ExtractedImageArtifact
from app.service.rag.ingestion.docling.table_image_vlm.models import TableImageVlmRuntime
from app.service.rag.ingestion.docling_chunker import (
    split_parent_child_chunks_from_docling_blocks,
)


PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _install_fake_docling_converter(monkeypatch, *, markdown_text: str) -> None:
    fake_docling_pkg = types.ModuleType("docling")
    fake_converter_module = types.ModuleType("docling.document_converter")

    class _FakeDocument:
        def export_to_markdown(self):
            return markdown_text

    class _FakeConvertResult:
        def __init__(self):
            self.document = _FakeDocument()

    class DocumentConverter:
        def convert(self, _temp_file_path):
            return _FakeConvertResult()

    fake_converter_module.DocumentConverter = DocumentConverter
    monkeypatch.setitem(sys.modules, "docling", fake_docling_pkg)
    monkeypatch.setitem(sys.modules, "docling.document_converter", fake_converter_module)


def _prepare_artifact_dir(tmp_path):
    run_id = "run-pptexcel"
    artifact_dir = tmp_path / "docling_artifacts" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = artifact_dir / "document.md"
    return run_id, artifact_dir, markdown_path


def _image_artifact(artifact_dir: Path, *, image_uuid: str, page_no: int) -> ExtractedImageArtifact:
    images_dir = artifact_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    image_path = images_dir / f"{image_uuid}.png"
    image_path.write_bytes(b"PNG")
    return ExtractedImageArtifact(
        kind="picture",
        image_uuid=image_uuid,
        file_name=image_path.name,
        file_path=str(image_path),
        page_no=page_no,
        picture_index=1,
    )


def test_parse_pptexcel_writes_standard_artifacts_and_no_duplicate_image_injection(
    monkeypatch, tmp_path
):
    _install_fake_docling_converter(
        monkeypatch,
        markdown_text="# Deck Title\n\n## Slide 1\n\nTitle &amp; Intro\n\n* bullet item",
    )

    run_id, artifact_dir, markdown_path = _prepare_artifact_dir(tmp_path)
    monkeypatch.setattr(
        extractor.local_artifacts_store,
        "prepare_docling_artifact_dir",
        lambda **_: (run_id, artifact_dir, markdown_path),
    )

    image = _image_artifact(artifact_dir, image_uuid="img-1", page_no=1)
    monkeypatch.setattr(
        extractor,
        "_extract_images_from_pptx",
        lambda **_: ([image], {1: ["img-1"]}, {"failed": 0, "uploaded": 0, "skipped": 1}),
    )
    monkeypatch.setattr(extractor, "_might_contain_table", lambda _img: False)
    monkeypatch.setattr(extractor.table_image_vlm, "build_table_image_vlm_runtime", lambda **_: None)

    result = extractor.parse_pptexcel_with_docling(
        file_bytes=b"dummy-pptx-bytes",
        file_name="sample.pptx",
        content_type=PPTX_MIME,
        file_id="file-123",
    )

    assert result.artifact_run_id == run_id
    assert Path(result.artifact_dir) == artifact_dir
    assert markdown_path.exists()
    assert (artifact_dir / "manifest.json").exists()
    assert Path(result.artifact_dir, "images").exists()
    assert "&amp;" not in result.markdown_content
    assert markdown_path.read_text(encoding="utf-8") == result.markdown_content

    assert [block.block_index for block in result.structured_blocks] == list(
        range(len(result.structured_blocks))
    )
    marker_count = sum(
        block.content.count("<!-- image-uuid: img-1 -->")
        for block in result.structured_blocks
    )
    assert marker_count == 1


def test_parse_pptexcel_reuses_shared_vlm_finalize_and_markers(monkeypatch, tmp_path):
    _install_fake_docling_converter(
        monkeypatch,
        markdown_text="# Deck Title\n\n## Slide 1\n\nSurrounding context text.",
    )

    run_id, artifact_dir, markdown_path = _prepare_artifact_dir(tmp_path)
    monkeypatch.setattr(
        extractor.local_artifacts_store,
        "prepare_docling_artifact_dir",
        lambda **_: (run_id, artifact_dir, markdown_path),
    )

    image = _image_artifact(artifact_dir, image_uuid="img-table-1", page_no=1)
    monkeypatch.setattr(
        extractor,
        "_extract_images_from_pptx",
        lambda **_: ([image], {1: ["img-table-1"]}, {"failed": 0, "uploaded": 0, "skipped": 1}),
    )
    monkeypatch.setattr(extractor, "_might_contain_table", lambda _img: True)

    class _FakeHelper:
        @staticmethod
        def extract_table_json_from_image(*, image_path, api_key=None, output_dir=None, save_artifacts=True):
            _ = image_path, api_key, save_artifacts
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "output.json").write_text(
                '{"table_metadata":{},"columns":[],"data":[]}',
                encoding="utf-8",
            )
            return {"table_metadata": {}, "columns": [], "data": []}

        @staticmethod
        def extract_table_semantic_summary_from_image(
            *,
            image_path,
            context_before,
            context_after,
            api_key=None,
            output_dir=None,
            save_artifacts=True,
        ):
            _ = image_path, context_before, context_after, api_key, output_dir, save_artifacts
            return "Extracted table summary."

    monkeypatch.setattr(
        extractor.table_image_vlm,
        "build_table_image_vlm_runtime",
        lambda **_: TableImageVlmRuntime(
            helper_module=_FakeHelper,
            api_key="secret",
            artifact_root=artifact_dir,
            context_blocks=2,
            after_ready_blocks=1,
            max_workers=1,
        ),
    )
    monkeypatch.setattr(
        extractor,
        "persist_table_data_toon_artifacts",
        lambda **kwargs: (
            (kwargs["artifact_dir"] / "table_data").mkdir(parents=True, exist_ok=True),
            [
                (kwargs["artifact_dir"] / "table_data" / f"{job.image_artifact.image_uuid}.json").write_text(
                    "{}",
                    encoding="utf-8",
                )
                for job in kwargs["table_image_vlm_jobs"]
            ],
        ),
    )

    result = extractor.parse_pptexcel_with_docling(
        file_bytes=b"dummy-pptx-bytes",
        file_name="sample.pptx",
        content_type=PPTX_MIME,
        file_id="file-456",
    )

    table_blocks = [block for block in result.structured_blocks if block.is_table_image]
    assert table_blocks
    table_block_text = table_blocks[0].content
    assert "<!-- table-image-uuid: img-table-1 -->" in table_block_text
    assert "<!-- table-image-vlm-json-path:" in table_block_text
    assert "Table summary (VLM)" in table_block_text
    placeholder = extractor.table_image_vlm.table_image_vlm_summary_placeholder("img-table-1")
    assert placeholder not in table_block_text
    assert (artifact_dir / "table_image_vlm_results.json").exists()
    assert (artifact_dir / "table_data" / "img-table-1.json").exists()


def test_pptexcel_structured_blocks_feed_docling_chunker_with_artifact_flags(
    monkeypatch, tmp_path
):
    _install_fake_docling_converter(
        monkeypatch,
        markdown_text="# Deck Title\n\n## Slide 1\n\nContext before visuals.",
    )

    run_id, artifact_dir, markdown_path = _prepare_artifact_dir(tmp_path)
    monkeypatch.setattr(
        extractor.local_artifacts_store,
        "prepare_docling_artifact_dir",
        lambda **_: (run_id, artifact_dir, markdown_path),
    )

    image_table = _image_artifact(artifact_dir, image_uuid="img-table-2", page_no=1)
    image_picture = _image_artifact(artifact_dir, image_uuid="img-picture-2", page_no=1)
    monkeypatch.setattr(
        extractor,
        "_extract_images_from_pptx",
        lambda **_: (
            [image_table, image_picture],
            {1: ["img-table-2", "img-picture-2"]},
            {"failed": 0, "uploaded": 0, "skipped": 2},
        ),
    )
    monkeypatch.setattr(
        extractor,
        "_might_contain_table",
        lambda img: img.image_uuid == "img-table-2",
    )

    class _FakeHelper:
        @staticmethod
        def extract_table_json_from_image(*, image_path, api_key=None, output_dir=None, save_artifacts=True):
            _ = image_path, api_key, save_artifacts
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "output.json").write_text(
                '{"table_metadata":{},"columns":[],"data":[]}',
                encoding="utf-8",
            )
            return {"table_metadata": {}, "columns": [], "data": []}

        @staticmethod
        def extract_table_semantic_summary_from_image(
            *,
            image_path,
            context_before,
            context_after,
            api_key=None,
            output_dir=None,
            save_artifacts=True,
        ):
            _ = image_path, context_before, context_after, api_key, output_dir, save_artifacts
            return "Table summary from VLM."

    monkeypatch.setattr(
        extractor.table_image_vlm,
        "build_table_image_vlm_runtime",
        lambda **_: TableImageVlmRuntime(
            helper_module=_FakeHelper,
            api_key="secret",
            artifact_root=artifact_dir,
            context_blocks=2,
            after_ready_blocks=1,
            max_workers=1,
        ),
    )
    monkeypatch.setattr(
        extractor,
        "persist_table_data_toon_artifacts",
        lambda **kwargs: None,
    )

    result = extractor.parse_pptexcel_with_docling(
        file_bytes=b"dummy-pptx-bytes",
        file_name="sample.pptx",
        content_type=PPTX_MIME,
        file_id="file-789",
    )

    parent_chunks, child_chunks = split_parent_child_chunks_from_docling_blocks(
        blocks=result.structured_blocks,
        file_name="sample.pptx",
        artifact_dir=result.artifact_dir,
        file_id=result.file_id,
    )
    assert parent_chunks
    assert child_chunks

    child_payloads = [chunk.model_dump() for chunk in child_chunks]
    assert any(
        payload.get("content_flags", {}).get("is_table_image")
        and payload.get("artifact_refs", {}).get("table_image_uuid") == "img-table-2"
        for payload in child_payloads
    )
    assert any(
        payload.get("content_flags", {}).get("is_image")
        and payload.get("artifact_refs", {}).get("image_uuid") == "img-picture-2"
        for payload in child_payloads
    )
