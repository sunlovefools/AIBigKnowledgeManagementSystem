import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.ingestion import docling_pdf_extractor as extractor


class _Status:
    FAILURE = "failure"
    SKIPPED = "skipped"
    PARTIAL_SUCCESS = "partial_success"
    SUCCESS = "success"


class _Prov:
    def __init__(self, page_no: int):
        self.page_no = page_no


class _FakeImage:
    def save(self, fp, _fmt):
        fp.write(b"PNG")


class _Picture:
    def __init__(self, page_no: int):
        self.prov = [_Prov(page_no)]

    def get_image(self, _doc):
        return _FakeImage()


class _TableData:
    def __init__(self, num_rows: int, num_cols: int):
        self.num_rows = num_rows
        self.num_cols = num_cols


class _Table:
    def __init__(self, page_no: int, num_rows: int, num_cols: int):
        self.prov = [_Prov(page_no)]
        self.data = _TableData(num_rows=num_rows, num_cols=num_cols)

    def get_image(self, _doc):
        return _FakeImage()


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
        return _Serialized(getattr(item, "_text", ""))


class _Document:
    def __init__(self, items):
        self._items = items

    def iterate_items(self):
        return [(item, None) for item in self._items]


class _Result:
    def __init__(self, status, items=None, errors=None):
        self.status = status
        self.document = _Document(items or [])
        self.errors = errors or []


class _Converter:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def convert(self, path, raises_on_error=False, page_range=None):
        self.calls.append((path, raises_on_error, page_range))
        return self._results.pop(0)


def _mock_runtime():
    return {
        "ConversionStatus": _Status,
        "MarkdownDocSerializer": _Serializer,
        "PictureItem": _Picture,
        "TableItem": _Table,
    }


def test_parse_pdf_with_docling_preview_writes_markdown_images_and_manifest(monkeypatch, tmp_path):
    chunk1_items = [
        _Text("Intro paragraph", page_no=1),
        _Picture(page_no=1),
        _Table(page_no=1, num_rows=0, num_cols=0),
    ]
    chunk2_items = [
        _Picture(page_no=7),
        _Text("Second chunk text", page_no=7),
    ]
    converter = _Converter(
        [
            _Result(status=_Status.PARTIAL_SUCCESS, items=chunk1_items, errors=["warn-1"]),
            _Result(status=_Status.SUCCESS, items=chunk2_items),
            _Result(status=_Status.FAILURE, items=[]),
        ]
    )

    monkeypatch.setattr(extractor, "_load_docling_runtime", _mock_runtime)
    monkeypatch.setattr(extractor, "_build_converter", lambda _runtime: converter)

    result = extractor.parse_pdf_with_docling_preview(
        pdf_bytes=b"%PDF-1.4 fake",
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

    picture_items = [img for img in result.images if img.kind == "picture"]
    assert len(picture_items) == 2
    assert len({img.file_name for img in picture_items}) == 2

    table_fallbacks = [img for img in result.images if img.kind == "table_fallback"]
    assert len(table_fallbacks) == 1
    assert result.stats.converted_chunks == 2
    assert result.stats.partial_failure_chunks == 1
    assert len(result.partial_failures) == 1
    assert result.partial_failures[0].page_range == "1-6"


def test_parse_pdf_with_docling_preview_raises_when_no_successful_chunks(monkeypatch, tmp_path):
    converter = _Converter([_Result(status=_Status.FAILURE, items=[])])
    monkeypatch.setattr(extractor, "_load_docling_runtime", _mock_runtime)
    monkeypatch.setattr(extractor, "_build_converter", lambda _runtime: converter)

    with pytest.raises(RuntimeError):
        extractor.parse_pdf_with_docling_preview(
            pdf_bytes=b"%PDF-1.4 fake",
            file_name="sample.pdf",
            artifact_root=tmp_path,
        )


def test_safe_stem_sanitizes_filename():
    assert extractor._safe_stem("A B/C:*report?.pdf") == "A_B_C_report"

