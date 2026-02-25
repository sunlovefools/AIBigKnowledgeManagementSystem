import sys
from pathlib import Path

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.ingestion import docling_pdf_extractor as extractor
from app.service.rag.ingestion import docling_pdf_extractor_beam as beam_extractor
from app.service.rag.ingestion import docling_pdf_extractor_local as local_extractor


class _Prov:
    def __init__(self, page_no: int):
        self.page_no = page_no


class _Picture:
    def __init__(self, page_no: int):
        self.prov = [_Prov(page_no)]


class _TableData:
    def __init__(self, num_rows: int | None, num_cols: int | None):
        self.num_rows = num_rows
        self.num_cols = num_cols


class _Table:
    def __init__(self, page_no: int, num_rows: int | None, num_cols: int | None):
        self.prov = [_Prov(page_no)]
        self.data = _TableData(num_rows=num_rows, num_cols=num_cols)


class _Text:
    def __init__(self, text: str, page_no: int):
        self._text = text
        self.prov = [_Prov(page_no)]


class _Serialized:
    def __init__(self, text: str):
        self.text = text


class _Serializer:
    def __init__(self, doc):
        self.doc = doc

    def serialize(self, item):
        if isinstance(item, _Picture):
            return _Serialized("<!-- image -->")
        if isinstance(item, _Table):
            return _Serialized("| a | b |")
        return _Serialized(getattr(item, "_text", ""))


class _Document:
    def __init__(self, items):
        self._items = items

    def iterate_items(self):
        return [(item, None) for item in self._items]


def _minimal_pdf_bytes() -> bytes:
    doc = fitz.open()
    doc.new_page(width=300, height=300)
    return doc.tobytes()


def _pdf_bytes_with_pages(page_count: int) -> bytes:
    doc = fitz.open()
    for _ in range(page_count):
        doc.new_page(width=300, height=300)
    return doc.tobytes()


def _mock_runtime_for_doc(doc: _Document):
    class _DoclingDocument:
        @staticmethod
        def model_validate(_payload):
            return doc

    return {
        "DoclingDocument": _DoclingDocument,
        "MarkdownDocSerializer": _Serializer,
        "PictureItem": _Picture,
        "TableItem": _Table,
    }


def _endpoint_response_for_items(items, *, status="success", errors=None, server_notes=None):
    ordered_items = []
    for seq, item in enumerate(items):
        record = {
            "seq": seq,
            "page_no": 1,
            "bbox": {"l": 10, "t": 10, "r": 50, "b": 50, "coord_origin": "TOPLEFT"},
            "table_info": None,
        }
        if isinstance(item, _Table):
            record["table_info"] = {"num_rows": item.data.num_rows, "num_cols": item.data.num_cols}
        ordered_items.append(record)

    return {
        "ok": True,
        "status": status,
        "errors": errors or [],
        "meta": {"filename": "sample.pdf"},
        "conversion_result_dump": {"document": {"fake": True}},
        "ordered_items": ordered_items,
        "server_notes": server_notes or [],
    }


def _patch_endpoint_runtime(monkeypatch, items, *, status="success", errors=None, server_notes=None):
    doc = _Document(items)
    monkeypatch.setenv("DOCLING_PDF_BACKEND", "beam")
    monkeypatch.setattr(beam_extractor, "_load_docling_module_runtime", lambda: _mock_runtime_for_doc(doc))
    monkeypatch.setattr(
        beam_extractor,
        "_call_beam_docling_endpoint",
        lambda pdf_bytes, file_name: _endpoint_response_for_items(
            items,
            status=status,
            errors=errors,
            server_notes=server_notes,
        ),
    )


def test_parse_pdf_with_docling_preview_writes_markdown_images_and_manifest(monkeypatch, tmp_path):
    items = [
        _Text("Intro paragraph", page_no=1),
        _Picture(page_no=1),
        _Table(page_no=1, num_rows=0, num_cols=0),
        _Picture(page_no=1),
        _Text("Second chunk text", page_no=1),
    ]
    _patch_endpoint_runtime(
        monkeypatch,
        items,
        status="partial_success",
        errors=["warn-1"],
        server_notes=["retry used"],
    )
    monkeypatch.setattr(beam_extractor, "_crop_image_bytes_from_endpoint_item", lambda *args, **kwargs: b"PNG")

    result = extractor.parse_pdf_with_docling_preview(
        pdf_bytes=_minimal_pdf_bytes(),
        file_name="Client Portfolio Analysis Report.pdf",
        artifact_root=tmp_path,
    )

    artifact_dir = Path(result.artifact_dir)
    assert artifact_dir.exists()
    assert Path(result.markdown_path).exists()
    assert (artifact_dir / "manifest.json").exists()

    markdown_text = Path(result.markdown_path).read_text(encoding="utf-8")
    assert "Intro paragraph" in markdown_text
    assert "Second chunk text" in markdown_text
    assert "Table (image): Structure extraction failed" in markdown_text
    assert "<!-- image -->" not in markdown_text

    picture_items = [img for img in result.images if img.kind == "picture"]
    assert len(picture_items) == 2
    for img in picture_items:
        assert f"<!-- image-uuid: {img.image_uuid} -->" in markdown_text

    table_images = [img for img in result.images if img.kind == "table_image"]
    assert len(table_images) == 1
    assert f"<!-- table-image-uuid: {table_images[0].image_uuid} -->" in markdown_text
    assert result.stats.converted_chunks == 1
    assert result.stats.partial_failure_chunks == 1
    assert len(result.partial_failures) == 1
    assert result.partial_failures[0].page_range == "full-document"
    assert any("Beam: retry used" in w for w in result.warnings)


def test_parse_pdf_with_docling_preview_raises_when_endpoint_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCLING_PDF_BACKEND", "beam")
    monkeypatch.setattr(beam_extractor, "_call_beam_docling_endpoint", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("endpoint boom")))

    with pytest.raises(RuntimeError, match="endpoint boom"):
        extractor.parse_pdf_with_docling_preview(
            pdf_bytes=_minimal_pdf_bytes(),
            file_name="sample.pdf",
            artifact_root=tmp_path,
        )


def test_parse_pdf_with_docling_preview_picture_crop_failure_keeps_markdown(monkeypatch, tmp_path):
    items = [_Picture(page_no=1)]
    _patch_endpoint_runtime(monkeypatch, items)
    monkeypatch.setattr(beam_extractor, "_crop_image_bytes_from_endpoint_item", lambda *args, **kwargs: None)

    result = extractor.parse_pdf_with_docling_preview(
        pdf_bytes=_minimal_pdf_bytes(),
        file_name="sample.pdf",
        artifact_root=tmp_path,
    )

    assert result.stats.pictures_extracted == 0
    assert "<!-- image -->" not in result.markdown_text
    assert "<!-- image-crop-failed -->" in result.markdown_text
    assert any("Failed to export picture" in warning for warning in result.warnings)


def test_safe_stem_sanitizes_filename():
    assert beam_extractor._safe_stem("A B/C:*report?.pdf") == "A_B_C_report"


class _FakeResponse:
    def __init__(self, *, ok=True, status_code=200, text="", json_data=None, json_exc=None):
        self.ok = ok
        self.status_code = status_code
        self.text = text
        self._json_data = json_data
        self._json_exc = json_exc

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json_data


def test_call_beam_docling_endpoint_raises_on_empty_body(monkeypatch):
    monkeypatch.setenv("BEAM_DOCLING_ENDPOINT", "https://example.test/beam")
    monkeypatch.setenv("BEAM_DOCLING_ENDPOINT_TOKEN", "secret")
    monkeypatch.setattr(
        beam_extractor.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(ok=True, status_code=200, text="   ", json_data=None),
    )

    with pytest.raises(RuntimeError, match="empty response body"):
        beam_extractor._call_beam_docling_endpoint(b"%PDF", "sample.pdf")


def test_call_beam_docling_endpoint_raises_on_non_json(monkeypatch):
    monkeypatch.setenv("BEAM_DOCLING_ENDPOINT", "https://example.test/beam")
    monkeypatch.setenv("BEAM_DOCLING_ENDPOINT_TOKEN", "secret")
    monkeypatch.setattr(
        beam_extractor.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(ok=True, status_code=200, text="<html>bad</html>", json_exc=ValueError("bad json")),
    )

    with pytest.raises(RuntimeError, match="non-JSON"):
        beam_extractor._call_beam_docling_endpoint(b"%PDF", "sample.pdf")


def test_call_beam_docling_endpoint_raises_when_missing_document(monkeypatch):
    monkeypatch.setenv("BEAM_DOCLING_ENDPOINT", "https://example.test/beam")
    monkeypatch.setenv("BEAM_DOCLING_ENDPOINT_TOKEN", "secret")
    monkeypatch.setattr(
        beam_extractor.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            ok=True,
            status_code=200,
            text="{}",
            json_data={"ok": True, "conversion_result_dump": {}, "ordered_items": []},
        ),
    )

    with pytest.raises(RuntimeError, match="conversion_result_dump.document"):
        beam_extractor._call_beam_docling_endpoint(b"%PDF", "sample.pdf")


def test_call_beam_docling_endpoint_raises_on_error_envelope(monkeypatch):
    monkeypatch.setenv("BEAM_DOCLING_ENDPOINT", "https://example.test/beam")
    monkeypatch.setenv("BEAM_DOCLING_ENDPOINT_TOKEN", "secret")
    monkeypatch.setattr(
        beam_extractor.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            ok=True,
            status_code=200,
            text='{"ok":false}',
            json_data={
                "ok": False,
                "status": "error",
                "error_code": "INVALID_REQUEST",
                "error_message": "bad input",
            },
        ),
    )

    with pytest.raises(RuntimeError, match="error response"):
        beam_extractor._call_beam_docling_endpoint(b"%PDF", "sample.pdf")


def test_parse_pdf_with_docling_preview_records_s3_success(monkeypatch, tmp_path):
    items = [_Picture(page_no=1)]
    _patch_endpoint_runtime(monkeypatch, items)
    monkeypatch.setattr(beam_extractor, "_crop_image_bytes_from_endpoint_item", lambda *args, **kwargs: b"PNG")
    monkeypatch.setenv("AWS_S3_UPLOAD_ENABLED", "true")
    monkeypatch.setattr(beam_extractor, "_load_s3_config", lambda: type("Cfg", (), {"prefix": "docling-previews"})())
    monkeypatch.setattr(
        beam_extractor,
        "build_s3_image_key",
        lambda image_uuid, extension=".png", prefix=None, source_file_name=None: f"{prefix}/images/{image_uuid}{extension}",
    )

    class _UploadResult:
        bucket = "test-bucket"
        key = "docling-previews/run/images/uuid.png"
        region = "ap-southeast-1"
        s3_uri = "s3://test-bucket/docling-previews/run/images/uuid.png"

    monkeypatch.setattr(beam_extractor, "upload_file_to_s3", lambda **kwargs: _UploadResult())

    result = extractor.parse_pdf_with_docling_preview(
        pdf_bytes=_minimal_pdf_bytes(),
        file_name="sample.pdf",
        artifact_root=tmp_path,
    )
    assert len(result.images) == 1
    img = result.images[0]
    assert img.image_uuid
    assert img.s3_upload_status == "uploaded"
    assert img.s3_bucket == "test-bucket"
    assert img.s3_key is not None
    assert img.s3_uri is not None


def test_parse_pdf_with_docling_preview_records_s3_failure_as_warning(monkeypatch, tmp_path):
    items = [_Picture(page_no=1)]
    _patch_endpoint_runtime(monkeypatch, items)
    monkeypatch.setattr(beam_extractor, "_crop_image_bytes_from_endpoint_item", lambda *args, **kwargs: b"PNG")
    monkeypatch.setenv("AWS_S3_UPLOAD_ENABLED", "true")
    monkeypatch.setattr(beam_extractor, "_load_s3_config", lambda: type("Cfg", (), {"prefix": "docling-previews"})())
    monkeypatch.setattr(
        beam_extractor,
        "build_s3_image_key",
        lambda image_uuid, extension=".png", prefix=None, source_file_name=None: f"{prefix}/images/{image_uuid}{extension}",
    )
    monkeypatch.setattr(
        beam_extractor,
        "upload_file_to_s3",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("S3 upload boom")),
    )

    result = extractor.parse_pdf_with_docling_preview(
        pdf_bytes=_minimal_pdf_bytes(),
        file_name="sample.pdf",
        artifact_root=tmp_path,
    )
    assert len(result.images) == 1
    img = result.images[0]
    assert img.s3_upload_status == "failed"
    assert "S3 upload boom" in (img.s3_error or "")
    assert any("Failed to upload picture" in warning for warning in result.warnings)


def test_parse_pdf_with_docling_preview_marks_s3_skipped_when_disabled(monkeypatch, tmp_path):
    items = [_Picture(page_no=1)]
    _patch_endpoint_runtime(monkeypatch, items)
    monkeypatch.setattr(beam_extractor, "_crop_image_bytes_from_endpoint_item", lambda *args, **kwargs: b"PNG")
    monkeypatch.setenv("AWS_S3_UPLOAD_ENABLED", "false")

    result = extractor.parse_pdf_with_docling_preview(
        pdf_bytes=_minimal_pdf_bytes(),
        file_name="sample.pdf",
        artifact_root=tmp_path,
    )
    assert len(result.images) == 1
    assert result.images[0].s3_upload_status == "skipped"


def test_docling_backend_selector_defaults_to_beam(monkeypatch):
    monkeypatch.delenv("DOCLING_PDF_BACKEND", raising=False)
    assert extractor._get_docling_pdf_backend() == "beam"


def test_docling_backend_selector_invalid_falls_back_to_beam(monkeypatch):
    monkeypatch.setenv("DOCLING_PDF_BACKEND", "weird")
    assert extractor._get_docling_pdf_backend() == "beam"


def test_parse_pdf_with_docling_preview_dispatches_to_local_backend(monkeypatch, tmp_path):
    sentinel = object()
    monkeypatch.setenv("DOCLING_PDF_BACKEND", "local")
    monkeypatch.setattr(
        local_extractor,
        "parse_pdf_with_docling_preview_local",
        lambda **kwargs: sentinel,
    )

    result = extractor.parse_pdf_with_docling_preview(
        pdf_bytes=b"%PDF-1.4",
        file_name="sample.pdf",
        artifact_root=tmp_path,
    )
    assert result is sentinel


def test_parse_pdf_with_docling_preview_dispatches_to_beam_by_default(monkeypatch, tmp_path):
    sentinel = object()
    monkeypatch.delenv("DOCLING_PDF_BACKEND", raising=False)
    monkeypatch.setattr(beam_extractor, "parse_pdf_with_docling_preview", lambda **kwargs: sentinel)

    result = extractor.parse_pdf_with_docling_preview(
        pdf_bytes=b"%PDF-1.4",
        file_name="sample.pdf",
        artifact_root=tmp_path,
    )
    assert result is sentinel


def test_local_converter_build_uses_requested_pipeline_config(monkeypatch):
    captured = {}

    class _InputFormat:
        PDF = "pdf"

    class _AcceleratorOptions:
        def __init__(self, device, num_threads, cuda_use_flash_attention2):
            self.device = device
            self.num_threads = num_threads
            self.cuda_use_flash_attention2 = cuda_use_flash_attention2

    class _ThreadedPdfPipelineOptions:
        pass

    class _PdfFormatOption:
        def __init__(self, pipeline_options):
            self.pipeline_options = pipeline_options
            captured["pipeline_options"] = pipeline_options

    class _DocumentConverter:
        def __init__(self, format_options):
            self.format_options = format_options
            captured["format_options"] = format_options

    monkeypatch.setattr(
        local_extractor,
        "_load_local_docling_runtime",
        lambda: {
            "InputFormat": _InputFormat,
            "AcceleratorOptions": _AcceleratorOptions,
            "ThreadedPdfPipelineOptions": _ThreadedPdfPipelineOptions,
            "DocumentConverter": _DocumentConverter,
            "PdfFormatOption": _PdfFormatOption,
            "MarkdownDocSerializer": object,
            "PictureItem": object,
            "TableItem": object,
            "DocumentStream": object,
        },
    )

    converter = local_extractor._build_local_converter()
    assert isinstance(converter, _DocumentConverter)

    pipeline = captured["pipeline_options"]
    assert pipeline.accelerator_options.device == "cuda"
    assert pipeline.accelerator_options.num_threads == 8
    assert pipeline.accelerator_options.cuda_use_flash_attention2 is False
    assert pipeline.do_ocr is False
    assert pipeline.do_table_structure is True
    assert pipeline.generate_table_images is True
    assert pipeline.generate_picture_images is True
    assert pipeline.images_scale == 2.0
    assert pipeline.generate_page_images is False
    assert pipeline.do_chart_extraction is False
    assert pipeline.do_formula_enrichment is False
    assert pipeline.layout_batch_size == 16
    assert pipeline.table_batch_size == 16


def test_local_backend_processes_in_fixed_chunks_and_serializes(monkeypatch, tmp_path):
    class _LocalPicture(_Picture):
        def get_image(self, _doc):
            return _FakePILImage()

    class _LocalTable(_Table):
        def get_image(self, _doc):
            return _FakePILImage()

    class _FakePILImage:
        def save(self, fp, _fmt):
            fp.write(b"PNG")

    class _LocalSerializer(_Serializer):
        def serialize(self, item):
            if isinstance(item, _LocalPicture):
                return _Serialized("<!-- image -->")
            if isinstance(item, _LocalTable):
                return _Serialized("| a | b |")
            return super().serialize(item)

    class _DocumentStream:
        def __init__(self, name, stream):
            self.name = name
            self.stream = stream

    class _Status:
        def __init__(self, value):
            self.value = value

    class _Result:
        def __init__(self, status, document, errors=None, page_count=None):
            self.status = _Status(status)
            self.document = document
            self.errors = errors or []
            self.input = None if page_count is None else type("Input", (), {"page_count": page_count})()

    chunk1_doc = _Document(
        [
            _Text("Chunk 1 text", page_no=1),
            _LocalPicture(page_no=1),
            _LocalTable(page_no=1, num_rows=0, num_cols=0),
        ]
    )
    chunk2_doc = _Document([_Text("Chunk 2 text", page_no=21)])

    responses = {
        (1, 20): _Result("success", chunk1_doc, page_count=21),
        (21, 21): _Result("partial_success", chunk2_doc, errors=["minor warning"], page_count=21),
    }

    class _FakeConverter:
        def __init__(self, responses):
            self.responses = responses
            self.calls = []

        def convert(self, _doc_stream, raises_on_error=False, page_range=None):
            assert raises_on_error is False
            self.calls.append(page_range)
            return self.responses[page_range]

    fake_converter = _FakeConverter(responses)

    monkeypatch.setenv("AWS_S3_UPLOAD_ENABLED", "false")
    monkeypatch.setattr(
        local_extractor,
        "_load_local_docling_runtime",
        lambda: {
            "MarkdownDocSerializer": _LocalSerializer,
            "PictureItem": _LocalPicture,
            "TableItem": _LocalTable,
            "DocumentStream": _DocumentStream,
        },
    )
    monkeypatch.setattr(local_extractor, "_get_or_create_local_converter", lambda: fake_converter)

    result = local_extractor.parse_pdf_with_docling_preview_local(
        pdf_bytes=_pdf_bytes_with_pages(21),
        file_name="sample.pdf",
        artifact_root=tmp_path,
        page_chunk_size=20,
    )

    assert fake_converter.calls == [(1, 20), (21, 21)]
    assert result.stats.converted_chunks == 2
    assert result.stats.partial_failure_chunks == 1
    assert "Chunk 1 text" in result.markdown_text
    assert "Chunk 2 text" in result.markdown_text
    assert "<!-- image-uuid:" in result.markdown_text
    assert "<!-- table-image-uuid:" in result.markdown_text
    assert result.stats.pictures_extracted == 1
    assert result.stats.table_fallback_images_extracted == 1


def test_local_backend_raises_when_no_chunks_convert(monkeypatch, tmp_path):
    class _DocumentStream:
        def __init__(self, name, stream):
            self.name = name
            self.stream = stream

    class _Status:
        def __init__(self, value):
            self.value = value

    class _Result:
        def __init__(self, status, errors=None):
            self.status = _Status(status)
            self.document = _Document([])
            self.errors = errors or []

    class _FakeConverter:
        def convert(self, _doc_stream, raises_on_error=False, page_range=None):
            return _Result("failure", errors=["boom"])

    monkeypatch.setattr(
        local_extractor,
        "_load_local_docling_runtime",
        lambda: {
            "MarkdownDocSerializer": _Serializer,
            "PictureItem": _Picture,
            "TableItem": _Table,
            "DocumentStream": _DocumentStream,
        },
    )
    monkeypatch.setattr(local_extractor, "_get_or_create_local_converter", lambda: _FakeConverter())

    with pytest.raises(RuntimeError, match="No pages converted successfully with local Docling"):
        local_extractor.parse_pdf_with_docling_preview_local(
            pdf_bytes=_minimal_pdf_bytes(),
            file_name="sample.pdf",
            artifact_root=tmp_path,
        )
