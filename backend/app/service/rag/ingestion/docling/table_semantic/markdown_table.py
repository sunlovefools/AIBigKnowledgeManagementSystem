"""
Helpers for parsing markdown pipe tables and flattening layout tables.
"""

from __future__ import annotations

import re

from .models import ParsedMarkdownTable


_DELIMITER_CELL_RE = re.compile(r"^\s*:?-{3,}:?\s*$")


def _split_markdown_row(line: str) -> list[str]:
    text = (line or "").strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _is_delimiter_row(line: str) -> bool:
    cells = _split_markdown_row(line)
    if not cells:
        return False
    return all(_DELIMITER_CELL_RE.match(cell or "") is not None for cell in cells)


def parse_markdown_table(table_markdown: str) -> ParsedMarkdownTable | None:
    """
    Parse a standard markdown pipe table.
    Returns None when input is not a parseable markdown table.
    """

    lines = [line.rstrip() for line in (table_markdown or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return None

    header_idx = -1
    delimiter_idx = -1

    for idx in range(len(lines) - 1):
        if "|" not in lines[idx]:
            continue
        if _is_delimiter_row(lines[idx + 1]):
            header_idx = idx
            delimiter_idx = idx + 1
            break

    if header_idx < 0 or delimiter_idx < 0:
        return None

    header_line = lines[header_idx]
    separator_line = lines[delimiter_idx]
    header_cells = _split_markdown_row(header_line)
    if not header_cells:
        return None

    body_lines = []
    for raw in lines[delimiter_idx + 1 :]:
        if "|" not in raw:
            continue
        body_lines.append(raw)

    rows: list[list[str]] = []
    for line in body_lines:
        row_cells = _split_markdown_row(line)
        if len(row_cells) < len(header_cells):
            row_cells += [""] * (len(header_cells) - len(row_cells))
        elif len(row_cells) > len(header_cells):
            row_cells = row_cells[: len(header_cells)]
        rows.append(row_cells)

    return ParsedMarkdownTable(
        headers=header_cells,
        rows=rows,
        markdown_header=header_line.strip(),
        markdown_separator=separator_line.strip(),
        markdown_rows=[line.strip() for line in body_lines],
    )


def build_table_sample_markdown(parsed: ParsedMarkdownTable, sample_rows: int) -> str:
    rows = parsed.markdown_rows[: max(sample_rows, 1)]
    sample_lines = [parsed.markdown_header, parsed.markdown_separator] + rows
    return "\n".join(sample_lines).strip()


def flatten_layout_table_to_bullets(parsed: ParsedMarkdownTable) -> str:
    """
    Flatten a layout table into key-value bullet text.
    """

    lines: list[str] = []
    headers = parsed.headers

    for row in parsed.rows:
        pairs = []
        for idx, header in enumerate(headers):
            header_text = (header or f"column_{idx + 1}").strip()
            value = (row[idx] if idx < len(row) else "").strip()
            if not header_text and not value:
                continue
            pairs.append(f"{header_text}: {value}".strip())
        if pairs:
            lines.append(f"- {'; '.join(pairs)}")

    if lines:
        return "\n".join(lines)

    # Fallback: preserve original table in plain text when row flattening yields empty output.
    return "\n".join([parsed.markdown_header, parsed.markdown_separator] + parsed.markdown_rows).strip()


def render_markdown_table_rows(
    *,
    header_line: str,
    separator_line: str,
    row_lines: list[str],
) -> str:
    """Render markdown table rows into a markdown table string."""
    lines = [header_line.strip(), separator_line.strip()] + [line.strip() for line in row_lines]
    return "\n".join([line for line in lines if line.strip()])

