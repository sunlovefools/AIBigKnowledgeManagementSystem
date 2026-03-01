"""Module purpose:
Provide structure-aware parent/child chunking for Docling blocks and emit
traceability markdown artifacts for generated chunks.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.id_utils import generate_uuid_v6
from app.service.rag.ingestion.chunker import ChildChunkModel, ParentChunkModel
from app.service.rag.ingestion.docling.common import (
    DoclingStructuredBlock,
    is_docling_artifacts_enabled,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TABLE_IMAGE_UUID_RE = re.compile(r"<!--\s*table-image-uuid:\s*([^\s]+)\s*-->")
_TABLE_IMAGE_JSON_PATH_RE = re.compile(
    r"<!--\s*table-image-vlm-json-path:\s*([^>]+?)\s*-->"
)
_TABLE_IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_TABLE_SUMMARY_RE = re.compile(
    r"^\s*>\s*\*\*Table summary \(VLM\)\*\*:\s*(.*)$",
    re.IGNORECASE,
)

_CONTEXT_BLOCK_TYPES = {"text", "list"}
_LONG_TEXT_BLOCK_TYPES = {"text", "list", "header"}


def _normalize_spaces(text: str) -> str:
    """Collapse whitespace to a single space for stable word counting."""

    return " ".join((text or "").split())


def _word_tokens(text: str) -> list[str]:
    """Return whitespace-tokenized words from normalized text."""

    normalized = _normalize_spaces(text)
    return normalized.split(" ") if normalized else []


def _word_count(text: str) -> int:
    """Return the word count using normalized whitespace tokenization."""

    return len(_word_tokens(text))


def _clean_text_for_context(text: str) -> str:
    """Strip inline markdown comments and normalize whitespace for context windows."""

    without_comments = _HTML_COMMENT_RE.sub(" ", text or "")
    return _normalize_spaces(without_comments)


def _take_first_words(text: str, count: int) -> str:
    """Return the first `count` words from text."""

    words = _word_tokens(text)
    return " ".join(words[:count])


def _take_last_words(text: str, count: int) -> str:
    """Return the last `count` words from text."""

    words = _word_tokens(text)
    return " ".join(words[-count:])


def _coerce_blocks(
    blocks: list[DoclingStructuredBlock] | list[dict[str, Any]],
) -> list[DoclingStructuredBlock]:
    """Validate/coerce incoming block payloads and keep non-empty content blocks only."""

    normalized: list[DoclingStructuredBlock] = []
    for raw in blocks or []:
        block = raw if isinstance(raw, DoclingStructuredBlock) else DoclingStructuredBlock.model_validate(raw)
        content = (block.content or "").strip()
        if not content:
            print(f"Skipping empty block of type '{block.block_type}' during coercion")
            continue
        normalized.append(block.model_copy(update={"content": content}))
    return normalized


def _build_sections(
    blocks: list[DoclingStructuredBlock],
) -> list[dict[str, Any]]:
    """
    Build logical sections for parent chunking.

    Rules:
    - leading non-header blocks become an intro section.
    - title/section headers start a section.
    - back-to-back headers are grouped as one header preamble until body appears.
    """

    if not blocks:
        return []

    # Find the first header and store its index
    first_header_idx: int | None = None
    for index, block in enumerate(blocks):
        if block.block_type == "header":
            first_header_idx = index
            break

    # No headers means the whole document is treated as one intro section.
    if first_header_idx is None:
        return [{"has_header": False, "preamble": [], "body": list(blocks)}]

    sections: list[dict[str, Any]] = []

    # Leading body blocks before first header become intro parent section.
    if first_header_idx > 0:
        sections.append(
            {
                "has_header": False,
                "preamble": [],
                "body": blocks[:first_header_idx],
            }
        )

    current_preamble: list[DoclingStructuredBlock] = []
    current_body: list[DoclingStructuredBlock] = []

    # Loop through the rest of the blocks starting from the first header index to build sections.
    for block in blocks[first_header_idx:]:
        if block.block_type == "header":
            # Previous block is a header and no other blocks are found in between
            if current_preamble and not current_body:
                # Consecutive headers stay together as one preamble.
                current_preamble.append(block)
                continue

            # If there is already a header and also body, and we still found another header, then it will be a parent chunk
            if current_preamble or current_body:
                sections.append(
                    {
                        "has_header": True,
                        "preamble": list(current_preamble),
                        "body": list(current_body),
                    }
                )
            current_preamble = [block] # Start a new preamble with the current header block.
            current_body = []
            continue
        
        # If not a header block, just add to the current body.
        current_body.append(block)

    # No more blocks to process, flush what remains as the last section.
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
    preamble: list[DoclingStructuredBlock],
    body: list[DoclingStructuredBlock],
    parent_max_words: int,
) -> list[dict[str, list[DoclingStructuredBlock]]]:
    """
    Split one section into parent-size-bounded parts by whole blocks.

    Parent flow keeps the preamble only at the beginning of the section.
    Child generation remains block-driven from body blocks, with preamble stored
    separately for child-prefix injection.
    """

    if not preamble and not body:
        return []

    if not body:
        return [
            {
                "parent_blocks": list(preamble),
                "child_blocks": list(preamble),
                "child_preamble": [],
            }
        ]

    parts: list[dict[str, list[DoclingStructuredBlock]]] = []
    preamble_words = sum(_word_count(block.content) for block in preamble)

    current_body_blocks: list[DoclingStructuredBlock] = []
    current_part_words = preamble_words
    is_first_part = True

    for block in body:
        block_words = _word_count(block.content)

        # Split only when this part already has body so whole blocks remain intact.
        if current_body_blocks and (current_part_words + block_words > parent_max_words):
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
                }
            )
            # Reset for the next part, but keep preamble for child generation.
            current_body_blocks = []
            current_part_words = 0
            is_first_part = False

        # Add the current block to the body of the current part if the current_body_block + current_part_words doesn't exceed the parent_max_words. 
        current_body_blocks.append(block)
        current_part_words += block_words

    # Process any remaining blocks after the loop ends as the last part.
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
            }
        )

    return parts


def _split_large_text_block(text: str, child_max_words: int) -> list[str]:
    """
    Split large text by sentence boundaries into <= child_max_words chunks.

    If sentence-based splitting is not possible or a sentence itself is oversized,
    keep the sentence intact as graceful fallback.
    """

    stripped = (text or "").strip()
    if not stripped:
        return []

    # Split sentences by punctuation for the entire texts
    sentences = [sentence.strip() for sentence in _SENTENCE_SPLIT_RE.split(stripped) if sentence.strip()]
    if len(sentences) <= 1:
        return [stripped]

    chunks: list[str] = []
    stored_sentences: list[str] = []
    current_words = 0

    # Loop through sentences and group into chunks based on word count, but keep sentences intact when possible.
    for sentence in sentences:
        current_sentence_words = _word_count(sentence)

        # If the current sentence word count and the current_words counts exceed the child_max_words, then we will flush the current sentences as a child chunk
        if stored_sentences and (current_words + current_sentence_words > child_max_words):
            chunks.append(" ".join(stored_sentences).strip())
            stored_sentences = [sentence]
            current_words = current_sentence_words
            continue

        # If the current_sentence's word + the current_words count doesn't exceed the child_max_words, 
        # then we will just add the sentence to the stored_sentences list and update the current_words count
        stored_sentences.append(sentence)
        current_words += current_sentence_words

    # Flush any remaining sentences as the last chunk after the loop ends.
    if stored_sentences:
        chunks.append(" ".join(stored_sentences).strip())

    return [chunk for chunk in chunks if chunk]


def _nearest_context_words(
    blocks: list[DoclingStructuredBlock],
    block_index: int,
    *,
    direction: str,
    context_words: int,
) -> str:
    """Find nearest text/list block in one direction and return up to 20 context words."""

    step = -1 if direction == "previous" else 1 # Set which direction of blocks to look at based on the direction
    index = block_index + step

    while 0 <= index < len(blocks):
        candidate = blocks[index]
        if candidate.block_type in _CONTEXT_BLOCK_TYPES:
            context_text = _clean_text_for_context(candidate.content)
            if not context_text:
                return ""
            if direction == "previous":
                return _take_last_words(context_text, context_words)
            return _take_first_words(context_text, context_words)
        index += step
    return ""


def _extract_table_summary(table_block_content: str) -> str:
    """Extract VLM table summary text from table-image markdown block."""

    for raw_line in (table_block_content or "").splitlines():
        line = raw_line.strip().lstrip(">").strip()
        normalized = _normalize_spaces(line)
        if normalized.lower().startswith("**table summary (vlm)**:"):
            summary = normalized.split(":", 1)[1].strip() if ":" in normalized else ""
            if summary:
                return summary
    return "Summary unavailable (see VLM artifacts/errors)."


def _build_picture_child_text(
    blocks: list[DoclingStructuredBlock],
    block_index: int,
    *,
    context_words: int,
) -> str:
    """Build picture child content with nearest text/list context."""

    picture_text = blocks[block_index].content.strip()

    # Get the contexts word from previous blocks
    previous_context = _nearest_context_words(
        blocks,
        block_index,
        direction="previous",
        context_words=context_words,
    )
    if previous_context:
        return "\n\n".join([previous_context, picture_text])

    # Get the contexts word from next blocks
    next_context = _nearest_context_words(
        blocks,
        block_index,
        direction="next",
        context_words=context_words,
    )
    if next_context:
        return "\n\n".join([picture_text, next_context])
    return picture_text


def _build_table_child_text(
    blocks: list[DoclingStructuredBlock],
    block_index: int,
    *,
    context_words: int,
) -> str:
    """Build table child content with prev/next text-list contexts and middle table content."""

    block = blocks[block_index]
    previous_context = _nearest_context_words(
        blocks,
        block_index,
        direction="previous",
        context_words=context_words,
    )
    next_context = _nearest_context_words(
        blocks,
        block_index,
        direction="next",
        context_words=context_words,
    )

    middle_text = (
        _extract_table_summary(block.content)
        if block.is_table_image
        else block.content.strip()
    )

    pieces = [piece for piece in [previous_context, middle_text, next_context] if piece]
    return "\n\n".join(pieces).strip()


def _build_child_candidates_for_block(
    blocks: list[DoclingStructuredBlock],
    block_index: int,
    *,
    child_max_words: int,
    context_words: int,
) -> list[str]:
    """Expand one block into one or more child chunk candidates before min-size merging."""

    block = blocks[block_index]
    content = block.content.strip()
    if not content:
        return []

    if block.block_type == "picture":
        return [_build_picture_child_text(blocks, block_index, context_words=context_words)]

    # Table blocks are always one child chunk and ignore the >80 split rule.
    if block.block_type == "table":
        table_text = _build_table_child_text(blocks, block_index, context_words=context_words)
        return [table_text] if table_text else []

    if block.block_type in _LONG_TEXT_BLOCK_TYPES and _word_count(content) > child_max_words:
        return _split_large_text_block(content, child_max_words)

    return [content]


def _merge_small_children(children: list[str], min_child_words: int) -> list[str]:
    """
    Merge tiny children into previous child within the same parent part.

    The word-count threshold applies to body-derived child text only.
    Section preamble text is injected after this merge step and is therefore
    excluded from tiny-chunk detection.

    If tiny chunk appears first, buffer and prepend to the next non-tiny child.
    """

    merged: list[str] = []
    leading_buffer = ""

    for child in children:
        chunk = child.strip()
        if not chunk:
            continue

        if _word_count(chunk) < min_child_words:
            if merged:
                merged[-1] = f"{merged[-1]}\n\n{chunk}"
            else:
                leading_buffer = f"{leading_buffer}\n\n{chunk}".strip() if leading_buffer else chunk
            continue

        if leading_buffer:
            chunk = f"{leading_buffer}\n\n{chunk}"
            leading_buffer = ""
        merged.append(chunk)

    if leading_buffer:
        if merged:
            merged[-1] = f"{merged[-1]}\n\n{leading_buffer}"
        else:
            merged.append(leading_buffer)

    return merged


def _compact_parent_table_blocks_for_artifact(
    content: str,
) -> tuple[str, list[dict[str, str]]]:
    """
    Compact verbose table-image blocks for parent artifact display only.

    Child chunks and stored parent content are not modified; this only changes
    how `parent_chunk.md` is rendered.
    """

    blocks = (content or "").split("\n\n")
    compacted_blocks: list[str] = []
    table_metadata: list[dict[str, str]] = []

    for block in blocks:
        text = block.strip()
        if "Table (image)" not in text:
            compacted_blocks.append(block)
            continue

        metadata: dict[str, str] = {}

        uuid_match = _TABLE_IMAGE_UUID_RE.search(text)
        if uuid_match:
            metadata["uuid"] = uuid_match.group(1).strip()

        json_match = _TABLE_IMAGE_JSON_PATH_RE.search(text)
        if json_match:
            metadata["json_path"] = json_match.group(1).strip()

        image_match = _TABLE_IMAGE_REF_RE.search(text)
        if image_match:
            image_caption = (image_match.group(1) or "").strip()
            image_path = (image_match.group(2) or "").strip()
            if image_caption:
                metadata["image_caption"] = image_caption
            if image_path:
                metadata["image_path"] = image_path

        summary_text = ""
        for line in text.splitlines():
            summary_match = _TABLE_SUMMARY_RE.match(line)
            if summary_match:
                summary_text = summary_match.group(1).strip()
                break
        if summary_text:
            metadata["summary"] = summary_text

        uuid_suffix = (
            f" with UUID of {metadata['uuid']}" if "uuid" in metadata else ""
        )
        compacted_blocks.append(
            f"> **Table (image)**: Table exists in image form{uuid_suffix}."
        )
        table_metadata.append(metadata)

    return "\n\n".join(compacted_blocks), table_metadata


def _prefix_children_with_preamble(
    children: list[str],
    preamble_blocks: list[DoclingStructuredBlock],
) -> list[str]:
    """Prefix each child chunk with section preamble text when preamble exists."""

    if not children or not preamble_blocks:
        return children

    preamble_text = "\n\n".join(
        block.content.strip() for block in preamble_blocks if block.content.strip()
    ).strip()
    if not preamble_text:
        return children

    return [f"{preamble_text}\n\n{child}".strip() for child in children]


def _write_parent_chunks_artifact(
    artifact_dir: Path,
    file_name: str,
    parent_chunks: list[ParentChunkModel],
) -> None:
    """Write parent chunk trace markdown file."""

    lines: list[str] = [
        "# Parent Chunks",
        "",
        f"- file_name: {file_name}",
        f"- total_parent_chunks: {len(parent_chunks)}",
        "",
    ]

    for index, parent in enumerate(parent_chunks):
        payload = parent.model_dump()
        content = payload["content"]
        compacted_content, table_metadata = _compact_parent_table_blocks_for_artifact(
            content
        )
        child_ids = payload["parent_chunk_metadata"].get("child_chunks_ids", [])
        child_ids_inline = ", ".join(child_ids) if child_ids else "(none)"
        lines.extend(
            [
                f"## Parent {index}",
                f"- parent_chunk_id: {payload['parent_chunk_id']}",
                f"- word_count: {_word_count(content)}",
                f"- child_chunks_ids: {child_ids_inline}",
            ]
        )
        if table_metadata:
            lines.append(f"- table_image_entries: {len(table_metadata)}")
            for entry_index, metadata in enumerate(table_metadata, start=1):
                prefix = f"- table_{entry_index}_"
                if "uuid" in metadata:
                    lines.append(f"{prefix}uuid: {metadata['uuid']}")
                if "image_caption" in metadata:
                    lines.append(f"{prefix}image_caption: {metadata['image_caption']}")
                if "image_path" in metadata:
                    lines.append(f"{prefix}image_path: {metadata['image_path']}")
                if "json_path" in metadata:
                    lines.append(f"{prefix}json_path: {metadata['json_path']}")
                if "summary" in metadata:
                    lines.append(f"{prefix}summary: {metadata['summary']}")
        lines.extend(
            [
                "```text",
                compacted_content,
                "```",
                "",
            ]
        )

    (artifact_dir / "parent_chunk.md").write_text("\n".join(lines), encoding="utf-8")


def _write_child_chunks_artifact(
    artifact_dir: Path,
    file_name: str,
    child_chunks: list[ChildChunkModel],
) -> None:
    """Write child chunk trace markdown file."""

    lines: list[str] = [
        "# Child Chunks",
        "",
        f"- file_name: {file_name}",
        f"- total_child_chunks: {len(child_chunks)}",
        "",
    ]

    for index, child in enumerate(child_chunks):
        payload = child.model_dump()
        content = payload["content"]
        parent_id = payload["child_chunk_metadata"].get("parent_id")
        lines.extend(
            [
                f"## Child {index}",
                f"- child_chunk_id: {payload['child_chunk_id']}",
                f"- parent_id: {parent_id}",
                f"- word_count: {_word_count(content)}",
                "```text",
                content,
                "```",
                "",
            ]
        )

    (artifact_dir / "child_chunk.md").write_text("\n".join(lines), encoding="utf-8")


def _write_trace_artifacts(
    artifact_dir: str | Path | None,
    file_name: str,
    parent_chunks: list[ParentChunkModel],
    child_chunks: list[ChildChunkModel],
) -> None:
    """Write parent/child markdown traces to the Docling artifact directory."""

    if not is_docling_artifacts_enabled():
        return

    if not artifact_dir:
        return

    path = Path(artifact_dir)
    path.mkdir(parents=True, exist_ok=True)
    _write_parent_chunks_artifact(path, file_name, parent_chunks)
    _write_child_chunks_artifact(path, file_name, child_chunks)


def split_parent_child_chunks_from_docling_blocks(
    blocks: list[DoclingStructuredBlock] | list[dict[str, Any]],
    file_name: str,
    artifact_dir: str | Path | None,
    *,
    parent_max_words: int = 500,
    child_max_words: int = 80,
    min_child_words: int = 20,
    context_words: int = 20,
) -> tuple[list[ParentChunkModel], list[ChildChunkModel]]:
    """
    Build parent/child chunks from normalized Docling blocks.

    Parent strategy:
    - header-based grouping with back-to-back header preambles.
    - 500-word split by whole blocks, without repeating preamble in later parent parts.

    Child strategy:
    - default one block per child with size/context special handling.
    - split parts still receive preamble at child-generation level.
    """

    normalized_blocks = _coerce_blocks(blocks)
    if not normalized_blocks:
        _write_trace_artifacts(artifact_dir, file_name, [], [])
        return [], []

    # Split the entire docling blocks into sections
    sections = _build_sections(normalized_blocks)
    
    # This parent_parts will store a list of dict with keys "parent_blocks", "child_blocks" and "child_preamble". 
    # The "parent_blocks" is the list of blocks that will be used to generate the parent chunk, 
    # the "child_blocks" is the list of blocks that will be used to generate the child chunks, 
    # and the "child_preamble" is the list of blocks that will be used as preamble for the child chunks 
    # (which is basically the same as the parent preamble but will be included in all child parts instead of just the first parent part).
    parent_parts: list[dict[str, list[DoclingStructuredBlock]]] = []

    # For each section, split into parent chunks
    for section in sections:
        parent_parts.extend(
            _split_section_into_parent_parts(
                preamble=section["preamble"],
                body=section["body"],
                parent_max_words=parent_max_words,
            )
        )

    if not parent_parts:
        _write_trace_artifacts(artifact_dir, file_name, [], [])
        return [], []

    parent_chunks: list[ParentChunkModel] = []
    child_chunks: list[ChildChunkModel] = []
    child_global_index = 0
    file_id = generate_uuid_v6()

    for parent_chunk_number, part in enumerate(parent_parts):
        parent_blocks = part["parent_blocks"]
        child_source_blocks = part["child_blocks"]
        child_preamble_blocks = part.get("child_preamble", [])
        parent_id = generate_uuid_v6()
        parent_content = "\n\n".join(
            block.content.strip() for block in parent_blocks if block.content.strip()
        ).strip()
        parent_child_ids: list[str] = []

        child_candidates: list[str] = []
        for index in range(len(child_source_blocks)):
            child_candidates.extend(
                _build_child_candidates_for_block(
                    child_source_blocks,
                    index,
                    child_max_words=child_max_words,
                    context_words=context_words,
                )
            )

        # Merge body-derived child chunks before preamble injection so tiny-chunk
        # detection does not include preamble words.
        merged_children = _merge_small_children(child_candidates, min_child_words)
        if not merged_children and parent_content:
            merged_children = [parent_content]
        merged_children = _prefix_children_with_preamble(
            merged_children,
            child_preamble_blocks,
        )

        for child_text in merged_children:
            child_id = generate_uuid_v6()
            parent_child_ids.append(child_id)
            child_chunks.append(
                ChildChunkModel(
                    child_chunk_id=child_id,
                    content=child_text.strip(),
                    file_metadata={
                        "file_name": file_name,
                        "file_id": file_id,
                    },
                    child_chunk_metadata={
                        "parent_id": parent_id,
                        "child_chunk_number": child_global_index,
                        "ingested_at": datetime.now(timezone.utc).isoformat(),
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
                    "file_id": file_id,
                },
                parent_chunk_metadata={
                    "child_chunks_ids": parent_child_ids,
                    "parent_chunk_number": parent_chunk_number,
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )

    _write_trace_artifacts(artifact_dir, file_name, parent_chunks, child_chunks)
    return parent_chunks, child_chunks
