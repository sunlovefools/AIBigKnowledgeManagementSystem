import ast
import sys
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


LEGACY_MODULE = "app.service.rag.ingestion.docling_pdf_extractor"
APP_ROOT = Path(__file__).resolve().parent.parent / "app"
SHIM_PATH = APP_ROOT / "service" / "rag" / "ingestion" / "docling_pdf_extractor.py"


def _collect_legacy_imports() -> list[str]:
    violations: list[str] = []

    for file_path in APP_ROOT.rglob("*.py"):
        if file_path.resolve() == SHIM_PATH.resolve():
            continue

        with tokenize.open(str(file_path)) as source_file:
            source = source_file.read()

        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            # Ignore unrelated syntax issues outside the scope of this import guard.
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "") == LEGACY_MODULE:
                rel_path = file_path.relative_to(APP_ROOT.parent)
                violations.append(f"{rel_path}:{node.lineno}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == LEGACY_MODULE:
                        rel_path = file_path.relative_to(APP_ROOT.parent)
                        violations.append(f"{rel_path}:{node.lineno}")

    return violations


def test_backend_app_does_not_import_legacy_docling_shim():
    violations = _collect_legacy_imports()
    assert not violations, (
        "Legacy docling import found; use `app.service.rag.ingestion.docling` instead:\n"
        + "\n".join(violations)
    )
