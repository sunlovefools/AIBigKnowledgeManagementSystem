import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.ingestion.markdown_chunker import split_parent_child_chunks_from_markdown


def _words(prefix: str, count: int, *, period: bool = False) -> str:
    body = " ".join(f"{prefix}{i}" for i in range(1, count + 1))
    return f"{body}." if period else body


def test_markdown_chunker_header_preamble_in_first_parent_only():
    content = "\n\n".join(
        [
            "# Report Title",
            "## Section A",
            _words("a", 220),
            _words("b", 220),
            _words("c", 220),
        ]
    )

    parents, children = split_parent_child_chunks_from_markdown(
        content,
        file_name="report.md",
        file_id="file-123",
        parent_max_words=300,
        child_max_words=120,
        min_child_words=20,
    )

    assert len(parents) >= 2
    assert parents[0].content.startswith("# Report Title\n\n## Section A")
    assert not parents[1].content.startswith("# Report Title\n\n## Section A")
    assert all(child.file_metadata["file_id"] == "file-123" for child in children)


def test_markdown_chunker_splits_large_text_by_sentence_and_merges_tiny_tail():
    long_sentences = " ".join(
        [
            _words("s1", 40, period=True),
            _words("s2", 40, period=True),
            _words("s3", 40, period=True),
            "tiny.",
        ]
    )

    parents, children = split_parent_child_chunks_from_markdown(
        long_sentences,
        file_name="notes.md",
        parent_max_words=500,
        child_max_words=80,
        min_child_words=10,
    )

    assert len(parents) == 1
    assert len(children) >= 2
    assert all(child.content.strip() for child in children)


def test_markdown_chunker_assigns_sequential_parent_numbers():
    content = "\n\n".join(
        [
            "# One",
            _words("x", 180),
            "# Two",
            _words("y", 180),
            "# Three",
            _words("z", 180),
        ]
    )

    parents, _children = split_parent_child_chunks_from_markdown(
        content,
        file_name="sequential.md",
        parent_max_words=200,
        child_max_words=90,
        min_child_words=20,
    )

    numbers = [parent.parent_chunk_metadata["parent_chunk_number"] for parent in parents]
    assert numbers == list(range(len(parents)))
