import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.ingestion import docling_pptexcel_extractor as extractor
from app.service.rag.ingestion.docling import pipeline as shared_pipeline
from app.service.rag.ingestion.docling.table_image_vlm.models import TableImageVlmRuntime
from app.service.rag.ingestion.docling_chunker import (
    split_parent_child_chunks_from_docling_blocks,
)


PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class _Prov:
    def __init__(self, page_no: int):
        self.page_no = page_no


class _Serialized:
    def __init__(self, text: str):
        self.text = text


class _TextItem:
    def __init__(self, text: str, page_no: int):
        self.text = text
        self.prov = [_Prov(page_no)]


class _ListItem(_TextItem):
    pass


class _SectionHeaderItem(_TextItem):
    pass


class _TitleItem(_TextItem):
    pass


class _PictureItem:
    def __init__(self, page_no: int):
        self.prov = [_Prov(page_no)]


class _TableData:
    def __init__(self, num_rows: int | None, num_cols: int | None):
        self.num_rows = num_rows
        self.num_cols = num_cols


class _TableItem:
    def __init__(
        self,
        page_no: int,
        num_rows: int | None,
        num_cols: int | None,
        *,
        self_ref: str | None = None,
        parent_ref: str | None = None,
    ):
        self.prov = [_Prov(page_no)]
        self.data = _TableData(num_rows=num_rows, num_cols=num_cols)
        self.self_ref = self_ref
        self.parent = _RefItem(parent_ref) if parent_ref else None


class _RefItem:
    def __init__(self, cref: str | None):
        self.cref = cref


class _Group:
    def __init__(self, *, self_ref: str, name: str, children: list[str]):
        self.self_ref = self_ref
        self.name = name
        self.children = [{"$ref": child_ref} for child_ref in children]


class _FakeSerializer:
    def __init__(self, doc):
        self.doc = doc

    def serialize(self, item):
        if isinstance(item, _PictureItem):
            return _Serialized("<!-- image -->")
        if isinstance(item, _TableItem):
            return _Serialized("| a | b |")
        if isinstance(item, _ListItem):
            return _Serialized(item.text)
        if isinstance(item, _SectionHeaderItem):
            return _Serialized(item.text)
        if isinstance(item, _TitleItem):
            return _Serialized(item.text)
        return _Serialized(getattr(item, "text", ""))


class _FakeDocument:
    def __init__(self, items, groups=None):
        self._items = list(items)
        self.groups = list(groups or [])

    def iterate_items(self):
        return [(item, None) for item in self._items]


class _FakeConvertResult:
    def __init__(self, document, status="success", errors=None):
        self.document = document
        self.status = status
        self.errors = errors or []


def _install_fake_docling(monkeypatch, *, items, groups=None) -> None:
    fake_docling_pkg = types.ModuleType("docling")
    fake_converter_module = types.ModuleType("docling.document_converter")

    class DocumentConverter:
        def convert(self, _temp_file_path):
            return _FakeConvertResult(document=_FakeDocument(items, groups=groups))

    fake_converter_module.DocumentConverter = DocumentConverter

    fake_docling_core_pkg = types.ModuleType("docling_core")
    fake_docling_core_transforms = types.ModuleType("docling_core.transforms")
    fake_docling_core_serializer = types.ModuleType("docling_core.transforms.serializer")
    fake_docling_core_serializer_markdown = types.ModuleType(
        "docling_core.transforms.serializer.markdown"
    )
    fake_docling_core_serializer_markdown.MarkdownDocSerializer = _FakeSerializer

    fake_docling_core_types = types.ModuleType("docling_core.types")
    fake_docling_core_types_doc = types.ModuleType("docling_core.types.doc")
    fake_docling_core_types_doc.ListItem = _ListItem
    fake_docling_core_types_doc.PictureItem = _PictureItem
    fake_docling_core_types_doc.SectionHeaderItem = _SectionHeaderItem
    fake_docling_core_types_doc.TableItem = _TableItem
    fake_docling_core_types_doc.TitleItem = _TitleItem

    monkeypatch.setitem(sys.modules, "docling", fake_docling_pkg)
    monkeypatch.setitem(sys.modules, "docling.document_converter", fake_converter_module)
    monkeypatch.setitem(sys.modules, "docling_core", fake_docling_core_pkg)
    monkeypatch.setitem(sys.modules, "docling_core.transforms", fake_docling_core_transforms)
    monkeypatch.setitem(
        sys.modules,
        "docling_core.transforms.serializer",
        fake_docling_core_serializer,
    )
    monkeypatch.setitem(
        sys.modules,
        "docling_core.transforms.serializer.markdown",
        fake_docling_core_serializer_markdown,
    )
    monkeypatch.setitem(sys.modules, "docling_core.types", fake_docling_core_types)
    monkeypatch.setitem(sys.modules, "docling_core.types.doc", fake_docling_core_types_doc)


def _prepare_artifact_dir(tmp_path):
    run_id = "run-pptexcel"
    artifact_dir = tmp_path / "docling_artifacts" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = artifact_dir / "document.md"
    return run_id, artifact_dir, markdown_path


def _patch_common_pipeline_hooks(monkeypatch):
    monkeypatch.setattr(
        shared_pipeline.local_client,
        "_extract_png_bytes_from_local_element",
        lambda _element, _document: b"PNG",
    )
    monkeypatch.setattr(
        shared_pipeline.s3_upload,
        "upload_image_artifact_to_s3",
        lambda artifact, **_: artifact.model_copy(update={"s3_upload_status": "skipped"}),
    )


def test_parse_pptexcel_writes_standard_artifacts_and_no_duplicate_image_injection(
    monkeypatch, tmp_path
):
    items = [
        _TitleItem("Deck Title", page_no=1),
        _TextItem("Title &amp; Intro", page_no=1),
        _ListItem("* bullet item", page_no=1),
        _PictureItem(page_no=1),
    ]
    _install_fake_docling(monkeypatch, items=items)
    _patch_common_pipeline_hooks(monkeypatch)

    run_id, artifact_dir, markdown_path = _prepare_artifact_dir(tmp_path)
    monkeypatch.setattr(
        extractor.local_artifacts_store,
        "prepare_docling_artifact_dir",
        lambda **_: (run_id, artifact_dir, markdown_path),
    )
    monkeypatch.setattr(shared_pipeline.table_image_vlm, "build_table_image_vlm_runtime", lambda **_: None)

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
    assert (artifact_dir / "images").exists()
    assert "&amp;" not in result.markdown_content
    assert markdown_path.read_text(encoding="utf-8") == result.markdown_content

    assert [block.block_index for block in result.structured_blocks] == list(
        range(len(result.structured_blocks))
    )

    picture = next(image for image in result.images if image.kind == "picture")
    picture_marker = f"<!-- image-uuid: {picture.image_uuid} -->"
    marker_count = sum(
        block.content.count(picture_marker)
        for block in result.structured_blocks
    )
    assert marker_count == 1


def test_parse_pptexcel_reuses_shared_vlm_finalize_and_markers(monkeypatch, tmp_path):
    items = [
        _TitleItem("Deck Title", page_no=1),
        _TextItem("Surrounding context text.", page_no=1),
        _TableItem(page_no=1, num_rows=0, num_cols=3),
        _TextItem("Trailing context block.", page_no=1),
    ]
    _install_fake_docling(monkeypatch, items=items)
    _patch_common_pipeline_hooks(monkeypatch)

    run_id, artifact_dir, markdown_path = _prepare_artifact_dir(tmp_path)
    monkeypatch.setattr(
        extractor.local_artifacts_store,
        "prepare_docling_artifact_dir",
        lambda **_: (run_id, artifact_dir, markdown_path),
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
            return "Extracted table summary."

    monkeypatch.setattr(
        shared_pipeline.table_image_vlm,
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
        shared_pipeline,
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

    table_image = next(image for image in result.images if image.kind == "table_image")
    table_blocks = [block for block in result.structured_blocks if block.is_table_image]
    assert table_blocks

    table_block_text = table_blocks[0].content
    assert f"<!-- table-image-uuid: {table_image.image_uuid} -->" in table_block_text
    assert "<!-- table-image-vlm-json-path:" in table_block_text
    assert "Table summary (VLM)" in table_block_text
    placeholder = shared_pipeline.table_image_vlm.table_image_vlm_summary_placeholder(
        table_image.image_uuid
    )
    assert placeholder not in table_block_text
    assert (artifact_dir / "table_image_vlm_results.json").exists()
    assert (artifact_dir / "table_data" / f"{table_image.image_uuid}.json").exists()


def test_pptexcel_structured_blocks_feed_docling_chunker_with_artifact_flags(
    monkeypatch, tmp_path
):
    items = [
        _TitleItem("Deck Title", page_no=1),
        _TextItem("Context before visuals.", page_no=1),
        _TableItem(page_no=1, num_rows=0, num_cols=2),
        _PictureItem(page_no=1),
        _TextItem("Context after visuals.", page_no=1),
    ]
    _install_fake_docling(monkeypatch, items=items)
    _patch_common_pipeline_hooks(monkeypatch)

    run_id, artifact_dir, markdown_path = _prepare_artifact_dir(tmp_path)
    monkeypatch.setattr(
        extractor.local_artifacts_store,
        "prepare_docling_artifact_dir",
        lambda **_: (run_id, artifact_dir, markdown_path),
    )
    monkeypatch.setattr(shared_pipeline.table_image_vlm, "build_table_image_vlm_runtime", lambda **_: None)

    result = extractor.parse_pptexcel_with_docling(
        file_bytes=b"dummy-pptx-bytes",
        file_name="sample.pptx",
        content_type=PPTX_MIME,
        file_id="file-789",
    )

    table_image_uuid = next(
        image.image_uuid for image in result.images if image.kind == "table_image"
    )
    picture_uuid = next(
        image.image_uuid for image in result.images if image.kind == "picture"
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
        and payload.get("artifact_refs", {}).get("table_image_uuid") == table_image_uuid
        for payload in child_payloads
    )
    assert any(
        payload.get("content_flags", {}).get("is_image")
        and payload.get("artifact_refs", {}).get("image_uuid") == picture_uuid
        for payload in child_payloads
    )


def test_excel_groups_inject_sheet_headers_as_chunk_preamble(monkeypatch, tmp_path):
    groups = [
        _Group(
            self_ref="#/groups/0",
            name="sheet: Titanic",
            children=["#/tables/0", "#/tables/1"],
        ),
        _Group(
            self_ref="#/groups/1",
            name="sheet: Notes",
            children=["#/tables/2"],
        ),
    ]
    items = [
        _TableItem(
            page_no=1,
            num_rows=2,
            num_cols=2,
            self_ref="#/tables/0",
            parent_ref="#/groups/0",
        ),
        _TableItem(
            page_no=1,
            num_rows=2,
            num_cols=2,
            self_ref="#/tables/1",
            parent_ref="#/groups/0",
        ),
        _TableItem(
            page_no=2,
            num_rows=2,
            num_cols=2,
            self_ref="#/tables/2",
            parent_ref="#/groups/1",
        ),
    ]
    _install_fake_docling(monkeypatch, items=items, groups=groups)
    _patch_common_pipeline_hooks(monkeypatch)

    run_id, artifact_dir, markdown_path = _prepare_artifact_dir(tmp_path)
    monkeypatch.setattr(
        extractor.local_artifacts_store,
        "prepare_docling_artifact_dir",
        lambda **_: (run_id, artifact_dir, markdown_path),
    )
    monkeypatch.setattr(
        shared_pipeline.table_image_vlm,
        "build_table_image_vlm_runtime",
        lambda **_: None,
    )

    result = extractor.parse_pptexcel_with_docling(
        file_bytes=b"dummy-xlsx-bytes",
        file_name="sample.xlsx",
        content_type=XLSX_MIME,
        file_id="file-xlsx-001",
    )

    header_blocks = [block for block in result.structured_blocks if block.block_type == "header"]
    assert any(block.content.strip() == "# Sheet: Titanic" for block in header_blocks)
    assert any(block.content.strip() == "# Sheet: Notes" for block in header_blocks)

    parent_chunks, child_chunks = split_parent_child_chunks_from_docling_blocks(
        blocks=result.structured_blocks,
        file_name="sample.xlsx",
        artifact_dir=result.artifact_dir,
        file_id=result.file_id,
    )

    assert any("# Sheet: Titanic" in chunk.content for chunk in parent_chunks)
    assert any("# Sheet: Notes" in chunk.content for chunk in parent_chunks)
    assert any("# Sheet: Titanic" in chunk.content for chunk in child_chunks)
    assert any("# Sheet: Notes" in chunk.content for chunk in child_chunks)
