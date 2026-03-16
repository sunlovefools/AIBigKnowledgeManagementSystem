"""Markdown-aware parent/child chunking for edited content.

This module provides a docling-inspired chunking strategy for plain markdown text:
- word-based parent and child sizing
- header-aware section grouping
- sentence-aware splitting for oversized text blocks
- small-child merge for semantic density
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.id_utils import generate_uuid_v6
from app.service.rag.ingestion.chunker import ChildChunkModel, ParentChunkModel

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_HEADER_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_CODE_FENCE_RE = re.compile(r"^\s*```")


@dataclass(frozen=True)
class MarkdownBlock:
    """A logical markdown block used for section and child construction."""

    block_type: str
    content: str


def _normalize_spaces(text: str) -> str:
    return " ".join((text or "").split())


def _word_count(text: str) -> int:
    normalized = _normalize_spaces(text)
    return len(normalized.split(" ")) if normalized else 0


def _tokenize_markdown_blocks(markdown: str) -> list[MarkdownBlock]:
    """Split markdown into paragraph-like blocks while preserving fenced code blocks."""

    lines = (markdown or "").splitlines()
    blocks: list[MarkdownBlock] = []
    buffer: list[str] = []
    in_fence = False

    def _flush_buffer() -> None:
        if not buffer:
            return
        text = "\n".join(buffer).strip()
        buffer.clear()
        if not text:
            return
        block_type = "header" if _HEADER_RE.match(text) else "text"
        blocks.append(MarkdownBlock(block_type=block_type, content=text))

    for raw_line in lines:
        line = raw_line.rstrip("\n")

        if _CODE_FENCE_RE.match(line):
            if in_fence:
                buffer.append(line)
                _flush_buffer()
                in_fence = False
                continue

            _flush_buffer()
            in_fence = True
            buffer.append(line)
            continue

        if in_fence:
            buffer.append(line)
            continue

        if not line.strip():
            _flush_buffer()
            continue

        if _HEADER_RE.match(line):
            _flush_buffer()
            blocks.append(MarkdownBlock(block_type="header", content=line.strip()))
            continue

        buffer.append(line)

    _flush_buffer()
    return [block for block in blocks if block.content.strip()]


def _build_sections(blocks: list[MarkdownBlock]) -> list[dict[str, list[MarkdownBlock] | bool]]:
    """Group markdown blocks into intro/header-based sections."""

    if not blocks:
        return []

    first_header_idx: int | None = None
    for index, block in enumerate(blocks):
        if block.block_type == "header":
            first_header_idx = index
            break

    if first_header_idx is None:
        return [{"has_header": False, "preamble": [], "body": list(blocks)}]

    sections: list[dict[str, list[MarkdownBlock] | bool]] = []
    if first_header_idx > 0:
        sections.append(
            {
                "has_header": False,
                "preamble": [],
                "body": blocks[:first_header_idx],
            }
        )

    current_preamble: list[MarkdownBlock] = []
    current_body: list[MarkdownBlock] = []

    for block in blocks[first_header_idx:]:
        if block.block_type == "header":
            if current_preamble and not current_body:
                current_preamble.append(block)
                continue

            if current_preamble or current_body:
                sections.append(
                    {
                        "has_header": True,
                        "preamble": list(current_preamble),
                        "body": list(current_body),
                    }
                )

            current_preamble = [block]
            current_body = []
            continue

        current_body.append(block)

    if current_preamble or current_body:
        sections.append(
            {
                "has_header": True,
                "preamble": list(current_preamble),
                "body": list(current_body),
            }
        )

    return sections


def _split_section_into_parent_parts(
    *,
    preamble: list[MarkdownBlock],
    body: list[MarkdownBlock],
    parent_max_words: int,
) -> list[dict[str, object]]:
    """Split one section into parent-size bounded parts by whole blocks."""

    if not preamble and not body:
        return []

    if not body:
        return [
            {
                "parent_blocks": list(preamble),
                "child_blocks": list(preamble),
                "child_preamble": [],
                "is_first_part": True,
            }
        ]

    parts: list[dict[str, object]] = []
    preamble_words = sum(_word_count(block.content) for block in preamble)
    current_body_blocks: list[MarkdownBlock] = []
    current_words = preamble_words
    is_first_part = True

    for block in body:
        block_words = _word_count(block.content)
        if current_body_blocks and (current_words + block_words > parent_max_words):
            parent_blocks = (
                list(preamble) + list(current_body_blocks)
                if is_first_part
                else list(current_body_blocks)
            )
            parts.append(
                {
                    "parent_blocks": parent_blocks,
                    "child_blocks": list(current_body_blocks),
                    "child_preamble": list(preamble),
                    "is_first_part": is_first_part,
                }
            )
            current_body_blocks = []
            current_words = 0
            is_first_part = False

        current_body_blocks.append(block)
        current_words += block_words

    if current_body_blocks:
        parent_blocks = (
            list(preamble) + list(current_body_blocks)
            if is_first_part
            else list(current_body_blocks)
        )
        parts.append(
            {
                "parent_blocks": parent_blocks,
                "child_blocks": list(current_body_blocks),
                "child_preamble": list(preamble),
                "is_first_part": is_first_part,
            }
        )

    return parts


def _split_large_text_block(text: str, child_max_words: int) -> list[str]:
    """Split oversized text by sentence boundaries while preserving sentence integrity."""

    stripped = (text or "").strip()
    if not stripped:
        return []

    sentences = [part.strip() for part in _SENTENCE_SPLIT_RE.split(stripped) if part.strip()]
    if len(sentences) <= 1:
        return [stripped]

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    for sentence in sentences:
        sentence_words = _word_count(sentence)
        if current and (current_words + sentence_words > child_max_words):
            chunks.append(" ".join(current).strip())
            current = [sentence]
            current_words = sentence_words
            continue

        current.append(sentence)
        current_words += sentence_words

    if current:
        chunks.append(" ".join(current).strip())

    return [chunk for chunk in chunks if chunk]


def _merge_small_children(children: list[str], min_child_words: int) -> list[str]:
    """Merge tiny children into the next chunk to avoid semantically weak vectors."""

    if not children:
        return []

    merged: list[str] = []
    carry = ""

    for index, child in enumerate(children):
        content = child.strip()
        if not content:
            continue

        if carry:
            content = f"{carry}\n\n{content}".strip()
            carry = ""

        is_last = index == len(children) - 1
        if _word_count(content) < min_child_words and not is_last:
            carry = content
            continue

        merged.append(content)

    if carry:
        if merged:
            merged[-1] = f"{merged[-1]}\n\n{carry}".strip()
        else:
            merged.append(carry)

    return merged


def split_parent_child_chunks_from_markdown(
    text: str,
    file_name: str,
    *,
    file_id: str | None = None,
    parent_max_words: int = 500,
    child_max_words: int = 80,
    min_child_words: int = 20,
) -> tuple[list[ParentChunkModel], list[ChildChunkModel]]:
    """Build parent and child chunks from markdown text using word-based constraints."""

    print(f"Splitting markdown into parent/child chunks")
    if not (text or "").strip():
        return [], []

    blocks = _tokenize_markdown_blocks(text)
    if not blocks:
        return [], []

    sections = _build_sections(blocks)
    parent_parts: list[dict[str, object]] = []
    for section in sections:
        parent_parts.extend(
            _split_section_into_parent_parts(
                preamble=list(section["preamble"]),
                body=list(section["body"]),
                parent_max_words=parent_max_words,
            )
        )

    if not parent_parts:
        return [], []

    resolved_file_id = file_id or generate_uuid_v6()
    parent_chunks: list[ParentChunkModel] = []
    child_chunks: list[ChildChunkModel] = []
    child_global_index = 0

    for parent_chunk_number, part in enumerate(parent_parts):
        parent_blocks = list(part["parent_blocks"])
        child_blocks = list(part["child_blocks"])
        child_preamble = list(part.get("child_preamble", []))
        is_first_part = bool(part.get("is_first_part", False))

        parent_id = generate_uuid_v6()
        parent_content = "\n\n".join(block.content.strip() for block in parent_blocks if block.content.strip()).strip()
        if not parent_content:
            continue

        child_texts: list[str] = []
        for block in child_blocks:
            content = block.content.strip()
            if not content:
                continue
            if block.block_type in {"text", "header"} and _word_count(content) > child_max_words:
                child_texts.extend(_split_large_text_block(content, child_max_words))
            else:
                child_texts.append(content)

        child_texts = _merge_small_children(child_texts, min_child_words)
        preamble_text = "\n\n".join(block.content.strip() for block in child_preamble if block.content.strip()).strip()

        parent_child_ids: list[str] = []
        for child_index, child_text in enumerate(child_texts):
            final_child_text = child_text
            has_preamble = False
            if preamble_text and not is_first_part:
                final_child_text = f"{preamble_text}\n\n{child_text}".strip()
                has_preamble = True

            child_id = generate_uuid_v6()
            parent_child_ids.append(child_id)

            child_chunks.append(
                ChildChunkModel(
                    child_chunk_id=child_id,
                    content=final_child_text,
                    file_metadata={
                        "file_name": file_name,
                        "file_id": resolved_file_id,
                    },
                    child_chunk_metadata={
                        "parent_id": parent_id,
                        "child_chunk_number": child_global_index,
                        "page_number": 0,
                        "has_preamble": has_preamble,
                        "ingested_at": datetime.now(timezone.utc).isoformat(),
                    },
                    content_flags={
                        "is_image": False,
                        "is_table_image": False,
                    },
                    artifact_refs={
                        "image_uuid": None,
                        "table_image_uuid": None,
                    },
                )
            )
            child_global_index += 1

        if not parent_child_ids:
            fallback_child_id = generate_uuid_v6()
            parent_child_ids.append(fallback_child_id)
            child_chunks.append(
                ChildChunkModel(
                    child_chunk_id=fallback_child_id,
                    content=parent_content,
                    file_metadata={
                        "file_name": file_name,
                        "file_id": resolved_file_id,
                    },
                    child_chunk_metadata={
                        "parent_id": parent_id,
                        "child_chunk_number": child_global_index,
                        "page_number": 0,
                        "has_preamble": False,
                        "ingested_at": datetime.now(timezone.utc).isoformat(),
                    },
                    content_flags={
                        "is_image": False,
                        "is_table_image": False,
                    },
                    artifact_refs={
                        "image_uuid": None,
                        "table_image_uuid": None,
                    },
                )
            )
            child_global_index += 1

        parent_chunks.append(
            ParentChunkModel(
                parent_chunk_id=parent_id,
                content=parent_content,
                file_metadata={
                    "file_name": file_name,
                    "file_id": resolved_file_id,
                },
                parent_chunk_metadata={
                    "child_chunks_ids": parent_child_ids,
                    "parent_chunk_number": parent_chunk_number,
                    "page_number": [0],
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                },
                content_flags={
                    "is_image": False,
                    "is_table_image": False,
                },
                artifact_refs={
                    "image_uuid": [],
                    "table_image_uuid": [],
                },
            )
        )

    return parent_chunks, child_chunks
