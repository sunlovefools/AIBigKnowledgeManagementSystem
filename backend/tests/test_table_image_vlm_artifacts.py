import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.ingestion.docling.table_image_vlm import artifacts


def test_write_json_file_falls_back_when_tmp_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    target = tmp_path / "semantic_or_response.json"
    original_write_text = Path.write_text

    def _patched_write_text(self: Path, content: str, encoding: str = "utf-8", **kwargs):
        if str(self).endswith(".tmp"):
            raise FileNotFoundError("simulated temp write failure")
        return original_write_text(self, content, encoding=encoding, **kwargs)

    monkeypatch.setattr(Path, "write_text", _patched_write_text, raising=True)

    artifacts._write_json_file(target, {"ok": True, "value": 42})
    parsed = json.loads(target.read_text(encoding="utf-8"))
    assert parsed["ok"] is True
    assert parsed["value"] == 42


def test_write_text_file_falls_back_when_tmp_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    target = tmp_path / "semantic_summary.txt"
    original_write_text = Path.write_text

    def _patched_write_text(self: Path, content: str, encoding: str = "utf-8", **kwargs):
        if str(self).endswith(".tmp"):
            raise FileNotFoundError("simulated temp write failure")
        return original_write_text(self, content, encoding=encoding, **kwargs)

    monkeypatch.setattr(Path, "write_text", _patched_write_text, raising=True)

    artifacts._write_text_file(target, "summary text")
    assert target.read_text(encoding="utf-8") == "summary text"
