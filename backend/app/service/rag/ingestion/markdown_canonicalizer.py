"""Utilities for deterministic markdown canonicalization across ingestion flows."""

from __future__ import annotations

import html
import re
from copy import deepcopy
from typing import Any

_HEADER_LINE_RE = re.compile(r"^(\s{0,3}#{1,6})\s*(.*?)\s*$")
_BULLET_LINE_RE = re.compile(r"^(\s*)([+*])\s+")
_ORDERED_LIST_LINE_RE = re.compile(r"^(\s*)(\d+)([.)])\s+")
_BLANK_LINE_STREAK_RE = re.compile(r"\n{3,}")
_CODE_FENCE_RE = re.compile(r"^\s*```")


def _normalize_newlines(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _rstrip_lines(text: str) -> str:
    return "\n".join(line.rstrip() for line in _normalize_newlines(text).split("\n"))


def _preserve_hardbreak_trailing_spaces(line: str) -> str:
    match = re.match(r"^(.*?)(\s+)$", line)
    if not match:
        return line

    content = match.group(1)
    trailing_spaces = match.group(2)
    if not content or not content.strip():
        return ""

    return f"{content}  " if len(trailing_spaces) >= 2 else content


def _canonicalize_non_fence_line(line: str, block_type: str | None = None) -> str:
    line = html.unescape(line).replace("\u00a0", " ")

    match = _HEADER_LINE_RE.match(line)
    if match and (block_type in {None, "header", "text", "list"}):
        header_marks = match.group(1)
        header_text = match.group(2).strip()
        return f"{header_marks} {header_text}" if header_text else header_marks

    if block_type in {None, "list", "text"}:
        line = _BULLET_LINE_RE.sub(r"\1- ", line)
        line = _ORDERED_LIST_LINE_RE.sub(r"\1\2\3 ", line)

    return line


def canonicalize_markdown_text(text: str) -> str:
    """Canonicalize markdown text while preserving fenced code blocks."""

    cleaned = _rstrip_lines(text)
    lines = cleaned.split("\n")
    in_fence = False
    normalized: list[str] = []

    for line in lines:
        if _CODE_FENCE_RE.match(line):
            in_fence = not in_fence
            normalized.append(line)
            continue

        if in_fence:
            normalized.append(line)
            continue

        normalized.append(_canonicalize_non_fence_line(line))

    canonical = "\n".join(normalized).strip("\n")
    canonical = _BLANK_LINE_STREAK_RE.sub("\n\n", canonical)
    return canonical


def normalize_markdown_for_modification(text: str) -> str:
    """Normalize editor markdown while preserving explicit hardbreak markers."""

    lines = _normalize_newlines(text).split("\n")
    in_fence = False
    normalized: list[str] = []

    for raw_line in lines:
        if _CODE_FENCE_RE.match(raw_line):
            in_fence = not in_fence
            normalized.append(raw_line)
            continue

        if in_fence:
            normalized.append(raw_line)
            continue

        line = _preserve_hardbreak_trailing_spaces(
            html.unescape(raw_line).replace("\u00a0", " ")
        )
        if not line:
            normalized.append("")
            continue

        suffix = "  " if line.endswith("  ") else ""
        line_body = line[:-2] if suffix else line
        normalized_body = _canonicalize_non_fence_line(line_body)
        normalized.append(f"{normalized_body}{suffix}")

    canonical = "\n".join(normalized).strip("\n")
    canonical = _BLANK_LINE_STREAK_RE.sub("\n\n", canonical)
    return canonical


def canonicalize_docling_block_text(block_type: str, text: str) -> str:
    """Canonicalize a Docling block by type without breaking image/table markers."""

    base = _rstrip_lines(text).strip("\n")
    if not base:
        return ""

    if block_type in {"picture", "table"}:
        return _BLANK_LINE_STREAK_RE.sub("\n\n", base)

    if block_type in {"header", "list", "text", "other"}:
        return canonicalize_markdown_text(base)

    return canonicalize_markdown_text(base)


def canonicalize_chunk_payloads_for_storage(
    parent_chunks: list[dict[str, Any]],
    child_chunks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Canonicalize chunk payload content before storage.

    The ingestion upload flow operates on one logical file at a time, so this
    helper assumes the input parent/child chunk lists belong to the same file.
    """

    normalized_parent_chunks = [deepcopy(chunk) for chunk in parent_chunks]
    normalized_child_chunks = [deepcopy(chunk) for chunk in child_chunks]

    for chunk in normalized_parent_chunks:
        chunk["content"] = canonicalize_markdown_text(str(chunk.get("content") or ""))

    for chunk in normalized_child_chunks:
        chunk["content"] = canonicalize_markdown_text(str(chunk.get("content") or ""))

    return normalized_parent_chunks, normalized_child_chunks
