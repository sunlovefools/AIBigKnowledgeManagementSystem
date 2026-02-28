import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.ingestion.docling.common import DoclingStructuredBlock
from app.service.rag.ingestion.docling_chunker import (
    split_parent_child_chunks_from_docling_blocks,
)


def _block(
    index: int,
    block_type: str,
    content: str,
    *,
    page_no: int | None = 1,
    is_table_image: bool = False,
    table_image_uuid: str | None = None,
) -> DoclingStructuredBlock:
    return DoclingStructuredBlock(
        block_index=index,
        block_type=block_type,
        content=content,
        page_no=page_no,
        is_table_image=is_table_image,
        table_image_uuid=table_image_uuid,
    )


def _words(prefix: str, n: int, *, end_with_period: bool = False) -> str:
    text = " ".join(f"{prefix}{i}" for i in range(1, n + 1))
    return f"{text}." if end_with_period else text


def test_parent_flow_keeps_preamble_once_but_children_repeat_per_split_part(tmp_path):
    blocks = [
        _block(0, "header", "Main Title"),
        _block(1, "header", "Section Alpha"),
        _block(2, "text", _words("b1w", 150)),
        _block(3, "text", _words("b2w", 150)),
        _block(4, "text", _words("b3w", 150)),
        _block(5, "text", _words("b4w", 150)),
        _block(6, "text", _words("b5w", 100)),
    ]

    parents, children = split_parent_child_chunks_from_docling_blocks(
        blocks=blocks,
        file_name="sample.pdf",
        artifact_dir=tmp_path,
    )

    assert len(parents) == 2
    assert children
    assert parents[0].content.startswith("Main Title\n\nSection Alpha")
    assert not parents[1].content.startswith("Main Title\n\nSection Alpha")

    children_by_parent = {}
    for child in children:
        parent_id = child.child_chunk_metadata["parent_id"]
        children_by_parent.setdefault(parent_id, []).append(child.content)

    parent_two_id = parents[1].parent_chunk_id
    assert any("Main Title" in content for content in children_by_parent[parent_two_id])
    assert any("Section Alpha" in content for content in children_by_parent[parent_two_id])


def test_intro_parent_before_first_header(tmp_path):
    blocks = [
        _block(0, "text", "Intro paragraph before any heading."),
        _block(1, "header", "Section One"),
        _block(2, "text", "Body text after section header."),
    ]

    parents, _children = split_parent_child_chunks_from_docling_blocks(
        blocks=blocks,
        file_name="sample.pdf",
        artifact_dir=tmp_path,
    )

    assert len(parents) == 2
    assert parents[0].content.startswith("Intro paragraph")
    assert parents[1].content.startswith("Section One")


def test_large_text_split_by_sentence_regex(tmp_path):
    sentence_a = _words("a", 35, end_with_period=True)
    sentence_b = _words("b", 35, end_with_period=True)
    sentence_c = _words("c", 35, end_with_period=True)
    long_text = f"{sentence_a} {sentence_b} {sentence_c}"
    blocks = [
        _block(0, "header", "Section For Long Block"),
        _block(1, "text", long_text),
    ]

    _parents, children = split_parent_child_chunks_from_docling_blocks(
        blocks=blocks,
        file_name="sample.pdf",
        artifact_dir=tmp_path,
    )

    assert len(children) == 2
    assert all("." in child.content for child in children)
    assert all(child.content.startswith("Section For Long Block") for child in children)


def test_small_child_merges_within_parent_only(tmp_path):
    blocks = [
        _block(0, "header", "Header One"),
        _block(1, "text", _words("p1", 40)),
        _block(2, "header", "Header Two"),
        _block(3, "text", "tiny block"),
        _block(4, "text", _words("p2", 40)),
    ]

    parents, children = split_parent_child_chunks_from_docling_blocks(
        blocks=blocks,
        file_name="sample.pdf",
        artifact_dir=tmp_path,
    )

    assert len(parents) == 2
    parent_one_id = parents[0].parent_chunk_id
    parent_two_id = parents[1].parent_chunk_id

    parent_one_children = [
        child.content
        for child in children
        if child.child_chunk_metadata["parent_id"] == parent_one_id
    ]
    parent_two_children = [
        child.content
        for child in children
        if child.child_chunk_metadata["parent_id"] == parent_two_id
    ]

    assert all("tiny block" not in child_text for child_text in parent_one_children)
    assert any("tiny block" in child_text for child_text in parent_two_children)


def test_picture_child_uses_nearest_text_context_skipping_table(tmp_path):
    previous_text = _words("prev", 25)
    blocks = [
        _block(0, "text", previous_text),
        _block(1, "table", "| col | val |\n| --- | --- |\n| a | 1 |"),
        _block(2, "picture", "<!-- image-uuid: image-123 -->"),
    ]

    _parents, children = split_parent_child_chunks_from_docling_blocks(
        blocks=blocks,
        file_name="sample.pdf",
        artifact_dir=tmp_path,
    )

    picture_children = [child for child in children if "image-123" in child.content]
    assert len(picture_children) == 1
    assert "prev6" in picture_children[0].content
    assert "prev25" in picture_children[0].content


def test_table_image_child_uses_vlm_summary_middle(tmp_path):
    table_block = "\n".join(
        [
            "> **Table (image)**: Table exists in image form.",
            "> <!-- table-image-uuid: uuid-1 -->",
            "> ![table](table.png)",
            "> **Table summary (VLM)**: Revenue increased by 18 percent quarter over quarter.",
        ]
    )
    blocks = [
        _block(0, "text", _words("before", 25)),
        _block(1, "table", table_block, is_table_image=True, table_image_uuid="uuid-1"),
        _block(2, "text", _words("after", 25)),
    ]

    _parents, children = split_parent_child_chunks_from_docling_blocks(
        blocks=blocks,
        file_name="sample.pdf",
        artifact_dir=tmp_path,
    )

    table_children = [child for child in children if "Revenue increased" in child.content]
    assert len(table_children) == 1
    assert "Table (image)" not in table_children[0].content
    assert "before6" in table_children[0].content
    assert "after20" in table_children[0].content


def test_serialized_table_child_keeps_full_table_without_length_split(tmp_path):
    long_table_text = "TABLE_START " + _words("cell", 120) + " TABLE_END"
    blocks = [
        _block(0, "text", _words("before", 25)),
        _block(1, "table", long_table_text, is_table_image=False),
        _block(2, "text", _words("after", 25)),
    ]

    _parents, children = split_parent_child_chunks_from_docling_blocks(
        blocks=blocks,
        file_name="sample.pdf",
        artifact_dir=tmp_path,
    )

    table_children = [child for child in children if "TABLE_START" in child.content]
    assert len(table_children) == 1
    assert "TABLE_END" in table_children[0].content
    assert "before6" in table_children[0].content
    assert "after20" in table_children[0].content


def test_trace_artifacts_are_written(tmp_path):
    blocks = [
        _block(0, "header", "Doc Header"),
        _block(1, "text", "Body content for trace artifact check."),
    ]

    parents, children = split_parent_child_chunks_from_docling_blocks(
        blocks=blocks,
        file_name="trace.pdf",
        artifact_dir=tmp_path,
    )

    assert parents
    assert children

    parent_md = (tmp_path / "parent_chunk.md")
    child_md = (tmp_path / "child_chunk.md")
    assert parent_md.exists()
    assert child_md.exists()

    parent_text = parent_md.read_text(encoding="utf-8")
    child_text = child_md.read_text(encoding="utf-8")
    assert "## Parent 0" in parent_text
    assert parents[0].parent_chunk_id in parent_text
    assert "## Child 0" in child_text
    assert children[0].child_chunk_id in child_text


def test_parent_artifact_compacts_table_markup_into_metadata_only(tmp_path):
    table_block = "\n".join(
        [
            "> **Table (image)**: Table exists in image form.",
            "> <!-- table-image-uuid: uuid-compact-1 -->",
            "> ![table-image](table-image.png)",
            "> <!-- table-image-vlm-json-path: table_image_vlm/table-1-uuid-compact-1/output.json -->",
            "> **Table summary (VLM)**: Revenue increased by 18 percent quarter over quarter.",
        ]
    )
    blocks = [
        _block(0, "header", "Visual Section"),
        _block(1, "table", table_block, is_table_image=True, table_image_uuid="uuid-compact-1"),
    ]

    _parents, _children = split_parent_child_chunks_from_docling_blocks(
        blocks=blocks,
        file_name="trace.pdf",
        artifact_dir=tmp_path,
    )

    parent_text = (tmp_path / "parent_chunk.md").read_text(encoding="utf-8")
    child_text = (tmp_path / "child_chunk.md").read_text(encoding="utf-8")

    assert "Table exists in image form with UUID of uuid-compact-1." in parent_text
    assert "table_1_uuid: uuid-compact-1" in parent_text
    assert "table_1_image_path: table-image.png" in parent_text
    assert (
        "table_1_json_path: table_image_vlm/table-1-uuid-compact-1/output.json"
        in parent_text
    )
    assert (
        "table_1_summary: Revenue increased by 18 percent quarter over quarter."
        in parent_text
    )
    assert "<!-- table-image-uuid:" not in parent_text
    assert "<!-- table-image-vlm-json-path:" not in parent_text
    assert "**Table summary (VLM)**" not in parent_text

    # Child artifact format remains unchanged (no parent metadata rows injected).
    assert "table_1_uuid:" not in child_text
