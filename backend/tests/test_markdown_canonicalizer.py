from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.ingestion.markdown_canonicalizer import (
    canonicalize_markdown_text,
    normalize_markdown_for_modification,
)


def test_canonicalize_markdown_text_normalizes_supported_editor_cases():
    raw = "##   Heading &amp; Detail  \r\n\r\n1.  First item\r\n\r\n* bullet\r\n\r\n\r\nTail  "

    assert canonicalize_markdown_text(raw) == (
        "## Heading & Detail\n\n1. First item\n\n- bullet\n\nTail"
    )


def test_canonicalize_markdown_text_preserves_code_fences():
    raw = "```python  \nprint('&amp;')  \n```\n\n* bullet"

    assert canonicalize_markdown_text(raw) == "```python\nprint('&amp;')\n```\n\n- bullet"


def test_normalize_markdown_for_modification_preserves_hardbreaks():
    raw = "Line 1  \r\nLine 2\r\n\r\n\r\n* bullet"

    assert normalize_markdown_for_modification(raw) == "Line 1  \nLine 2\n\n- bullet"


def test_normalize_markdown_for_modification_collapses_indented_blank_lines():
    raw = "##   Heading\n\n1.  First item\n    \n\n\n2.  Second item"

    assert normalize_markdown_for_modification(raw) == (
        "## Heading\n\n1. First item\n\n2. Second item"
    )
