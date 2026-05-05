import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

stub_vectordb = types.ModuleType("app.vectordb.vectordb")


async def _unused_upsert_documents(**kwargs):
    _ = kwargs
    return None


stub_vectordb.upsert_documents = _unused_upsert_documents
_original_vectordb_module = sys.modules.get("app.vectordb.vectordb")
sys.modules["app.vectordb.vectordb"] = stub_vectordb

from app.service.rag.ingestion.chunk_models import ChildChunkModel, ParentChunkModel
from app.service.rag.ingestion.docling.models import DoclingParseResult, DoclingStructuredBlock
from app.service.rag.ingestion.docling.table_semantic.markdown_table import (
    parse_markdown_table,
)
from app.service.rag.ingestion.docling.table_semantic.models import (
    DescriptionAndSections,
    TableClassification,
)
from app.service.rag.ingestion.docling.table_semantic import pipeline as table_semantic_pipeline
from app.service.rag.ingestion.docling.table_semantic.pipeline import (
    TableSemanticIngestionError,
    process_semantic_tables_for_pdf,
)
from app.service.rag.ingestion import ingest_upload_service

if _original_vectordb_module is not None:
    sys.modules["app.vectordb.vectordb"] = _original_vectordb_module
else:
    del sys.modules["app.vectordb.vectordb"]


def _make_markdown_table(headers: list[str], row_values: list[list[str]]) -> str:
    header = "| " + " | ".join(headers) + " |"
    divider = "| " + " | ".join(["---"] * len(headers)) + " |"
    rows = ["| " + " | ".join(values) + " |" for values in row_values]
    return "\n".join([header, divider] + rows)


def _block(
    index: int,
    block_type: str,
    content: str,
    *,
    page_no: int = 1,
    is_table_image: bool = False,
) -> DoclingStructuredBlock:
    return DoclingStructuredBlock(
        block_index=index,
        block_type=block_type,
        content=content,
        page_no=page_no,
        is_table_image=is_table_image,
    )


def test_layout_table_is_flattened_and_not_semantic_chunked(monkeypatch, tmp_path):
    monkeypatch.setenv("TABLE_SEMANTIC_INGESTION_ENABLED", "true")

    table_markdown = _make_markdown_table(
        ["Metric", "Definition"],
        [
            ["Growth", "Revenue prior to foreign currency impact."],
            ["Bookings", "New binding contractual agreements."],
        ],
    )
    blocks = [
        _block(0, "text", "Paragraph before table."),
        _block(1, "table", table_markdown),
        _block(2, "text", "Paragraph after table."),
    ]

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._classify_table",
        lambda **_: TableClassification(
            table_type="layout",
            needs_description=False,
            col_headers=["Metric", "Definition"],
            row_headers=[],
        ),
    )

    transformed_blocks, semantic_parents, semantic_children, warnings = (
        process_semantic_tables_for_pdf(
            blocks=blocks,
            file_name="layout.pdf",
            file_id="file-layout",
            artifact_dir=tmp_path,
        )
    )

    assert not warnings
    assert not semantic_parents
    assert not semantic_children
    assert transformed_blocks[1].block_type == "text"
    assert "- Metric: Growth; Definition: Revenue prior to foreign currency impact." in (
        transformed_blocks[1].content
    )


def test_matrix_table_builds_semantic_children_and_parents(monkeypatch, tmp_path):
    monkeypatch.setenv("TABLE_SEMANTIC_INGESTION_ENABLED", "true")
    headers = ["Region", "Q1", "Q2", "Q3"]
    rows = [[f"Region-{idx}", str(idx), str(idx + 1), str(idx + 2)] for idx in range(1, 26)]
    table_markdown = _make_markdown_table(headers, rows)
    blocks = [_block(0, "table", table_markdown)]

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._classify_table",
        lambda **_: TableClassification(
            table_type="matrix",
            needs_description=True,
            col_headers=headers,
            row_headers=["Region-1", "Region-2"],
        ),
    )
    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_description_and_sections",
        lambda **_: DescriptionAndSections(
            description="This table compares quarterly figures by region.",
            sections=[],
        ),
    )
    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_row_slice_summaries",
        lambda **_: ["slice-1", "slice-2", "slice-3"],
    )

    transformed_blocks, semantic_parents, semantic_children, _warnings = (
        process_semantic_tables_for_pdf(
            blocks=blocks,
            file_name="matrix.pdf",
            file_id="file-matrix",
            artifact_dir=tmp_path,
        )
    )

    assert transformed_blocks[0].block_type == "other"
    assert transformed_blocks[0].content == ""
    assert len(semantic_children) == 3
    assert len(semantic_parents) == 1

    first_child = semantic_children[0]
    assert first_child.content_flags["is_semantic_table"] is True
    assert first_child.child_chunk_metadata["table_slice"]["slice_index"] == 0
    assert "General Description: This table compares quarterly figures by region." in first_child.content

    first_parent = semantic_parents[0]
    table_semantic = first_parent.parent_chunk_metadata["table_semantic"]
    assert table_semantic["table_type"] == "matrix"
    assert table_semantic["child_rows_per_chunk"] == 10
    assert len(first_parent.parent_chunk_metadata["child_chunks_ids"]) == 3


def test_entity_list_uses_semantic_path(monkeypatch, tmp_path):
    monkeypatch.setenv("TABLE_SEMANTIC_INGESTION_ENABLED", "true")
    headers = ["Endpoint", "Method", "Description", "Rate Limit"]
    rows = [
        [f"/api/v1/items/{idx}", "GET", f"desc-{idx}", "100/min"]
        for idx in range(1, 7)
    ]
    table_markdown = _make_markdown_table(headers, rows)
    blocks = [_block(0, "table", table_markdown)]

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._classify_table",
        lambda **_: TableClassification(
            table_type="entity_list",
            needs_description=True,
            col_headers=headers,
            row_headers=["Endpoint"],
        ),
    )
    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_description_and_sections",
        lambda **_: DescriptionAndSections(
            description="This table describes API endpoints and limits.",
            sections=[],
        ),
    )
    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_row_slice_summaries",
        lambda **_: ["entity-slice"],
    )

    _transformed_blocks, semantic_parents, semantic_children, _warnings = (
        process_semantic_tables_for_pdf(
            blocks=blocks,
            file_name="entity.pdf",
            file_id="file-entity",
            artifact_dir=tmp_path,
        )
    )

    assert len(semantic_children) == 1
    assert len(semantic_parents) == 1
    assert (
        semantic_parents[0].parent_chunk_metadata["table_semantic"]["table_type"]
        == "entity_list"
    )


def test_wide_table_uses_five_rows_per_child_chunk(monkeypatch, tmp_path):
    monkeypatch.setenv("TABLE_SEMANTIC_INGESTION_ENABLED", "true")
    headers = [f"col_{idx}" for idx in range(1, 12)]
    rows = [[f"r{row}_c{col}" for col in range(1, 12)] for row in range(1, 12)]
    table_markdown = _make_markdown_table(headers, rows)
    blocks = [_block(0, "table", table_markdown)]
    captured = {"child_rows_per_chunk": None}

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._classify_table",
        lambda **_: TableClassification(
            table_type="matrix",
            needs_description=True,
            col_headers=headers,
            row_headers=[],
        ),
    )
    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_description_and_sections",
        lambda **_: DescriptionAndSections(
            description="Wide table summary.",
            sections=[],
        ),
    )

    def _fake_row_summaries(**kwargs):
        captured["child_rows_per_chunk"] = kwargs["child_rows_per_chunk"]
        return ["slice-1", "slice-2", "slice-3"]

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_row_slice_summaries",
        _fake_row_summaries,
    )

    _transformed_blocks, semantic_parents, semantic_children, _warnings = (
        process_semantic_tables_for_pdf(
            blocks=blocks,
            file_name="wide.pdf",
            file_id="file-wide",
            artifact_dir=tmp_path,
        )
    )

    assert captured["child_rows_per_chunk"] == 5
    assert len(semantic_children) == 3
    assert len(semantic_parents) == 1
    assert semantic_parents[0].parent_chunk_metadata["table_semantic"]["child_rows_per_chunk"] == 5


def test_rubric_table_builds_section_chunks(monkeypatch, tmp_path):
    monkeypatch.setenv("TABLE_SEMANTIC_INGESTION_ENABLED", "true")
    headers = ["Section", "Component", "Weight", "Criteria"]
    rows = [
        ["Interim Report", "Project Background & Understanding", "10%", "Evidence of understanding of the brief."],
        ["", "Requirements & Critical Analysis", "15%", "Depth, clarity, and prioritisation of requirements."],
        ["Main Report", "Project Background & Understanding", "12%", "Understanding of the project, scope, and subject area."],
        ["", "Requirements & Critical Analysis", "15%", "Depth, clarity, breadth of requirements and critical analysis."],
        ["", "Project Management & Progress", "15%", "Efficiency and consistency of project management and tools."],
        ["", "Reflection", "12%", "Technical, realisation, and management reflection."],
        ["", "Style", "6%", "Length, depth, clarity, structure, grammar, visuals, and layout."],
        ["Software User Manual", "Documentation for software engineer", "25%", "Architecture, design, implementation, and testing documentation."],
    ]
    table_markdown = _make_markdown_table(headers, rows)
    blocks = [_block(0, "table", table_markdown)]

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._classify_table",
        lambda **_: TableClassification(
            table_type="matrix",
            needs_description=True,
            col_headers=headers,
            row_headers=["Interim Report", "Main Report", "Software User Manual"],
        ),
    )
    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_description_and_sections",
        lambda **_: DescriptionAndSections(
            description="Rubric table for assessment criteria and mark weights.",
            sections=[
                {
                    "row_index": 0,
                    "section_name": "Interim Report",
                    "row_labels": ["Project Background", "Requirements"],
                    "subsections": [],
                },
                {
                    "row_index": 2,
                    "section_name": "Main Report",
                    "row_labels": [
                        "Project Background & Understanding",
                        "Requirements & Critical Analysis",
                        "Project Management & Progress",
                        "Reflection",
                        "Style",
                    ],
                    "subsections": [],
                },
                {
                    "row_index": 7,
                    "section_name": "Software User Manual",
                    "row_labels": ["Documentation for software engineer"],
                    "subsections": [],
                },
            ],
        ),
    )

    def _unexpected_row_summaries(**_kwargs):
        raise AssertionError("section chunking should not call row-slice summaries")

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_row_slice_summaries",
        _unexpected_row_summaries,
    )

    transformed_blocks, semantic_parents, semantic_children, _warnings = (
        process_semantic_tables_for_pdf(
            blocks=blocks,
            file_name="groupProjectRubric2025-2026.pdf",
            file_id="file-rubric",
            artifact_dir=tmp_path,
        )
    )

    assert transformed_blocks[0].block_type == "other"
    assert len(semantic_parents) == 3
    assert len(semantic_children) == 3

    main_report_parent = next(
        parent
        for parent in semantic_parents
        if parent.parent_chunk_metadata["table_semantic"]["section_name"] == "Main Report"
    )
    main_metadata = main_report_parent.parent_chunk_metadata["table_semantic"]
    assert main_metadata["section_chunking"] is True
    assert main_metadata["weights"] == ["12%", "15%", "6%"]
    assert main_metadata["criteria_names"][:5] == [
        "Project Background & Understanding",
        "Requirements & Critical Analysis",
        "Project Management & Progress",
        "Reflection",
        "Style",
    ]
    assert main_metadata["structured_rows"][0]["label"] == "Project Background & Understanding"
    assert main_metadata["structured_rows"][0]["weights"] == ["12%"]
    assert "Component" in main_metadata["structured_rows"][0]["cells"]
    assert "Project Background & Understanding" in main_report_parent.content
    assert "Requirements & Critical Analysis" in main_report_parent.content
    assert "Project Management & Progress" in main_report_parent.content
    assert "Reflection" in main_report_parent.content
    assert "Style" in main_report_parent.content
    assert "Section: Main Report" not in main_report_parent.content
    assert "Criteria Names:" not in main_report_parent.content

    main_report_child = next(
        child
        for child in semantic_children
        if child.child_chunk_metadata["table_slice"]["section_name"] == "Main Report"
    )
    assert "Section: Main Report" in main_report_child.content
    assert "Criteria Names:" in main_report_child.content
    assert "Criteria Names: Project Background & Understanding, Requirements & Critical Analysis, Project Management & Progress, Reflection, Style" in main_report_child.content
    assert "Technical, realisation, and management reflection." in main_report_child.content


def test_rubric_subsections_build_independent_chunks(monkeypatch, tmp_path):
    monkeypatch.setenv("TABLE_SEMANTIC_INGESTION_ENABLED", "true")
    headers = ["Section", "Component", "Weight", "Criteria"]
    rows = [
        ["Interim Report", "Summary", "5%", "Summarise progress."],
        ["", "Style", "5%", "Clear writing."],
        ["Software User Manual", "Documentation for software engineer / programmer", "25%", "Architecture, design, implementation, testing approaches."],
        ["", "Documentation for end user", "15%", "Installation, functionality, troubleshooting, FAQs."],
        ["Software Total", "Software", "100%", "Technical achievement and implementation quality."],
    ]
    table_markdown = _make_markdown_table(headers, rows)
    blocks = [_block(0, "table", table_markdown)]

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._classify_table",
        lambda **_: TableClassification(
            table_type="matrix",
            needs_description=True,
            col_headers=headers,
            row_headers=[],
        ),
    )
    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_description_and_sections",
        lambda **_: DescriptionAndSections(
            description="Rubric table for software manual criteria.",
            sections=[
                {"row_index": 0, "section_name": "Interim Report", "row_labels": ["Summary", "Style"], "subsections": []},
                {
                    "row_index": 2,
                    "section_name": "Software User Manual",
                    "row_labels": [],
                    "subsections": [
                        {
                            "row_index": 2,
                            "section_name": "Software Engineer Documentation",
                            "row_labels": ["Architecture and implementation documentation"],
                            "subsections": [],
                        },
                        {
                            "row_index": 3,
                            "section_name": "End User Documentation",
                            "row_labels": ["Installation and functionality documentation"],
                            "subsections": [],
                        },
                    ],
                },
                {"row_index": 4, "section_name": "Software Total", "row_labels": ["Software"], "subsections": []},
            ],
        ),
    )

    _transformed_blocks, semantic_parents, semantic_children, _warnings = (
        process_semantic_tables_for_pdf(
            blocks=blocks,
            file_name="groupProjectRubric2025-2026.pdf",
            file_id="file-rubric",
            artifact_dir=tmp_path,
        )
    )

    software_engineer_parent = next(
        parent
        for parent in semantic_parents
        if parent.parent_chunk_metadata["table_semantic"]["subsection_name"] == "Software Engineer Documentation"
    )
    end_user_parent = next(
        parent
        for parent in semantic_parents
        if parent.parent_chunk_metadata["table_semantic"]["subsection_name"] == "End User Documentation"
    )
    assert software_engineer_parent.parent_chunk_metadata["table_semantic"]["parent_section_name"] == "Software User Manual"
    assert software_engineer_parent.parent_chunk_metadata["table_semantic"]["is_subsection"] is True
    assert software_engineer_parent.parent_chunk_metadata["table_semantic"]["criteria_names"] == [
        "Architecture and implementation documentation"
    ]
    assert software_engineer_parent.parent_chunk_metadata["table_semantic"]["structured_rows"][0]["label"] == (
        "Architecture and implementation documentation"
    )
    assert "Documentation for software engineer / programmer" in software_engineer_parent.content
    assert "Architecture and implementation documentation" not in software_engineer_parent.content
    assert "Documentation for end user" not in software_engineer_parent.content
    assert "End User Documentation" not in end_user_parent.content
    assert "Documentation for end user" in end_user_parent.content
    software_engineer_child = next(
        child
        for child in semantic_children
        if child.child_chunk_metadata["table_slice"]["subsection_name"] == "Software Engineer Documentation"
    )
    assert "Architecture and implementation documentation" in software_engineer_child.content
    assert len(semantic_children) == len(semantic_parents)


def test_large_section_uses_row_slice_children_with_clean_parent(monkeypatch, tmp_path):
    monkeypatch.setenv("TABLE_SEMANTIC_INGESTION_ENABLED", "true")
    monkeypatch.setenv("TABLE_SEMANTIC_SECTION_SINGLE_CHUNK_MAX_ROWS", "8")
    headers = ["Section", "Component", "Weight", "Criteria"]
    rows = [
        ["Small Section", "Opening", "5%", "Short opening criteria."],
        ["", "Style", "5%", "Short style criteria."],
    ]
    rows.extend(
        [
            [
                "Big Section" if idx == 0 else "",
                f"Criterion {idx + 1}",
                "10%",
                f"Detailed criteria text {idx + 1}.",
            ]
            for idx in range(11)
        ]
    )
    rows.append(["Tail Section", "Summary", "5%", "Tail criteria."])
    table_markdown = _make_markdown_table(headers, rows)
    blocks = [_block(0, "table", table_markdown)]

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._classify_table",
        lambda **_: TableClassification(
            table_type="matrix",
            needs_description=True,
            col_headers=headers,
            row_headers=[],
        ),
    )
    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_description_and_sections",
        lambda **_: DescriptionAndSections(
            description="Rubric table with mixed section sizes.",
            sections=[
                {"row_index": 0, "section_name": "Small Section", "row_labels": ["Opening", "Style"], "subsections": []},
                {"row_index": 2, "section_name": "Big Section", "row_labels": ["Large criteria"], "subsections": []},
                {"row_index": 13, "section_name": "Tail Section", "row_labels": ["Summary"], "subsections": []},
            ],
        ),
    )

    captured = {"row_line_counts": []}

    def _fake_row_summaries(**kwargs):
        captured["row_line_counts"].append(len(kwargs["row_lines"]))
        return ["big-slice-0", "big-slice-1"]

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_row_slice_summaries",
        _fake_row_summaries,
    )

    _transformed_blocks, semantic_parents, semantic_children, _warnings = (
        process_semantic_tables_for_pdf(
            blocks=blocks,
            file_name="large-section.pdf",
            file_id="file-large-section",
            artifact_dir=tmp_path,
        )
    )

    big_parent = next(
        parent
        for parent in semantic_parents
        if parent.parent_chunk_metadata["table_semantic"]["section_name"] == "Big Section"
    )
    big_children = [
        child
        for child in semantic_children
        if child.child_chunk_metadata["table_slice"]["section_name"] == "Big Section"
    ]

    assert captured["row_line_counts"] == [11]
    assert len(big_children) == 2
    assert big_parent.parent_chunk_metadata["table_semantic"]["large_section_chunking"] is True
    assert big_parent.parent_chunk_metadata["table_semantic"]["structured_rows"][0]["label"] == "Criterion 1"
    assert len(big_parent.parent_chunk_metadata["child_chunks_ids"]) == 2
    assert "Section: Big Section" not in big_parent.content
    assert "Row Description:" not in big_parent.content
    assert "Criterion 11" in big_parent.content
    assert "Row Description: big-slice-0" in big_children[0].content
    assert "Row Description: big-slice-1" in big_children[1].content


def test_section_detection_cleans_total_names_and_skips_pure_aggregate_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("TABLE_SEMANTIC_INGESTION_ENABLED", "true")
    headers = ["Section", "Component", "Weight", "Criteria"]
    rows = [
        ["Feature Area Total:", "Login", "20%", "Criteria: secure login flow."],
        ["", "Recovery", "10%", "Criteria: recovery flow."],
        ["Grand Total:", "", "30%", ""],
        ["Billing Total:", "Invoices", "15%", "Criteria: invoice generation."],
        ["", "Refunds", "15%", "Criteria: refund handling."],
    ]
    table_markdown = _make_markdown_table(headers, rows)
    blocks = [_block(0, "table", table_markdown)]

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._classify_table",
        lambda **_: TableClassification(
            table_type="matrix",
            needs_description=True,
            col_headers=headers,
            row_headers=[],
        ),
    )
    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_description_and_sections",
        lambda **_: DescriptionAndSections(
            description="Feature criteria table.",
            sections=[
                {"row_index": 0, "section_name": "Feature Area Total:", "row_labels": ["Login", "Recovery"], "subsections": []},
                {"row_index": 2, "section_name": "Grand Total:", "row_labels": [], "subsections": []},
                {"row_index": 3, "section_name": "Billing Total:", "row_labels": ["Invoices", "Refunds"], "subsections": []},
            ],
        ),
    )

    def _unexpected_row_summaries(**_kwargs):
        raise AssertionError("small sections should not call row-slice summaries")

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_row_slice_summaries",
        _unexpected_row_summaries,
    )

    _transformed_blocks, semantic_parents, _semantic_children, _warnings = (
        process_semantic_tables_for_pdf(
            blocks=blocks,
            file_name="feature-criteria.pdf",
            file_id="file-feature-criteria",
            artifact_dir=tmp_path,
        )
    )

    section_names = [
        parent.parent_chunk_metadata["table_semantic"]["section_name"]
        for parent in semantic_parents
    ]
    assert section_names == ["Feature Area", "Billing"]
    assert all("Grand Total" not in parent.content for parent in semantic_parents)


def test_dense_item_signals_are_recorded_for_compound_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("TABLE_SEMANTIC_INGESTION_ENABLED", "true")
    headers = ["Section", "Criteria", "Weight"]
    compound_criteria = (
        "Alpha component (20%) Criteria: first set of expectations. "
        "Beta component (15%) Criteria: second set of expectations."
    )
    rows = [
        ["Overview", "Short criteria.", "5%"],
        ["Compound Section", compound_criteria, "20% 15%"],
        ["Tail", "Tail criteria.", "5%"],
    ]
    table_markdown = _make_markdown_table(headers, rows)
    blocks = [_block(0, "table", table_markdown)]

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._classify_table",
        lambda **_: TableClassification(
            table_type="matrix",
            needs_description=True,
            col_headers=headers,
            row_headers=[],
        ),
    )
    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_description_and_sections",
        lambda **_: DescriptionAndSections(
            description="Compound criteria table.",
            sections=[
                {"row_index": 0, "section_name": "Overview", "row_labels": ["Overview"], "subsections": []},
                {"row_index": 1, "section_name": "Compound Section", "row_labels": [], "subsections": []},
                {"row_index": 2, "section_name": "Tail", "row_labels": ["Tail"], "subsections": []},
            ],
        ),
    )

    _transformed_blocks, semantic_parents, _semantic_children, _warnings = (
        process_semantic_tables_for_pdf(
            blocks=blocks,
            file_name="compound.pdf",
            file_id="file-compound",
            artifact_dir=tmp_path,
        )
    )

    compound_parent = next(
        parent
        for parent in semantic_parents
        if parent.parent_chunk_metadata["table_semantic"]["section_name"] == "Compound Section"
    )
    density = compound_parent.parent_chunk_metadata["table_semantic"]["density"]
    assert density["has_dense_items"] is True
    assert density["item_extraction_recommended"] is True
    assert "multiple_weights" in density["dense_rows"][0]["signals"]
    assert "multiple_item_markers" in density["dense_rows"][0]["signals"]


def test_generic_sectioned_table_builds_section_chunks_without_rubric_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("TABLE_SEMANTIC_INGESTION_ENABLED", "true")
    headers = ["Area", "Topic", "Detail"]
    rows = [
        ["Authentication", "Login", "Password and SSO login flow."],
        ["", "Recovery", "Password reset and account recovery."],
        ["Billing", "Invoices", "Monthly invoice generation."],
        ["", "Refunds", "Refund request workflow."],
    ]
    table_markdown = _make_markdown_table(headers, rows)
    blocks = [_block(0, "table", table_markdown)]

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._classify_table",
        lambda **_: TableClassification(
            table_type="matrix",
            needs_description=True,
            col_headers=headers,
            row_headers=[],
        ),
    )
    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_description_and_sections",
        lambda **_: DescriptionAndSections(
            description="Feature areas and their implementation topics.",
            sections=[
                {"row_index": 0, "section_name": "Authentication", "row_labels": [], "subsections": []},
                {"row_index": 2, "section_name": "Billing", "row_labels": [], "subsections": []},
            ],
        ),
    )

    def _unexpected_row_summaries(**_kwargs):
        raise AssertionError("natural section tables should not call row-slice summaries")

    monkeypatch.setattr(
        "app.service.rag.ingestion.docling.table_semantic.pipeline._build_row_slice_summaries",
        _unexpected_row_summaries,
    )

    _transformed_blocks, semantic_parents, semantic_children, _warnings = (
        process_semantic_tables_for_pdf(
            blocks=blocks,
            file_name="feature_overview.pdf",
            file_id="file-sections",
            artifact_dir=tmp_path,
        )
    )

    assert len(semantic_parents) == 2
    assert len(semantic_children) == 2
    auth_parent = next(
        parent
        for parent in semantic_parents
        if parent.parent_chunk_metadata["table_semantic"]["section_name"] == "Authentication"
    )
    assert "Login" in auth_parent.content
    assert "Recovery" in auth_parent.content


def test_description_and_sections_prompt_uses_markdown_table_sample(monkeypatch):
    headers = ["Section", "Topic", "Detail"]
    rows = [
        ["", "continuation", "leading blank"],
        ["Section A", "alpha", "first section"],
        ["", "beta", "first section continuation"],
        ["Section B", "gamma", "second section"],
    ]
    parsed = parse_markdown_table(_make_markdown_table(headers, rows))
    assert parsed is not None
    captured_prompt = {"value": ""}

    def _fake_description_and_sections_call(**kwargs):
        captured_prompt["value"] = kwargs["user_prompt"]
        return {
            "description": "Sectioned feature table.",
            "has_sections": True,
            "sections": [{"row_index": 1, "section_name": "Section A"}],
        }

    monkeypatch.setattr(
        table_semantic_pipeline,
        "_llm_structured_json_call",
        _fake_description_and_sections_call,
    )

    result = table_semantic_pipeline._build_description_and_sections(
        parsed_table=parsed,
        classification=TableClassification(
            table_type="matrix",
            needs_description=True,
            col_headers=headers,
            row_headers=[],
        ),
        context_before="",
        context_after="",
    )

    assert result.description == "Sectioned feature table."
    assert result.sections[0]["section_name"] == "Section A"
    assert "Markdown table sample:" in captured_prompt["value"]
    assert "| Section | Topic | Detail |" in captured_prompt["value"]
    assert "| Section A | alpha | first section |" in captured_prompt["value"]
    assert "First-column cell values" not in captured_prompt["value"]


def test_row_summary_build_uses_explicit_presliced_prompt(monkeypatch):
    headers = ["Region", "Q1", "Q2"]
    rows = [[f"Region-{idx}", str(idx), str(idx + 1)] for idx in range(1, 13)]
    table_markdown = _make_markdown_table(headers, rows)
    parsed = parse_markdown_table(table_markdown)
    assert parsed is not None

    captured_user_prompt = {"value": ""}

    def _fake_row_summary_call(**kwargs):
        captured_user_prompt["value"] = kwargs["user_prompt"]
        return [
            {"slice_index": 0, "summary": "slice-0"},
            {"slice_index": 1, "summary": "slice-1"},
            {"slice_index": 2, "summary": "slice-2"},
        ]

    monkeypatch.setattr(
        table_semantic_pipeline,
        "_llm_structured_json_call",
        _fake_row_summary_call,
    )

    summaries = table_semantic_pipeline._build_row_slice_summaries(
        parsed_table=parsed,
        general_description="Quarterly revenue by region.",
        child_rows_per_chunk=5,
    )

    assert summaries == ["slice-0", "slice-1", "slice-2"]
    assert "Explicit Slices:" in captured_user_prompt["value"]
    assert "Slice 0:" in captured_user_prompt["value"]
    assert "Slice 1:" in captured_user_prompt["value"]
    assert "Slice 2:" in captured_user_prompt["value"]
    assert "| Region-11 | 11 | 12 |" in captured_user_prompt["value"]


def test_unparseable_table_is_treated_as_layout_without_llm(monkeypatch, tmp_path):
    monkeypatch.setenv("TABLE_SEMANTIC_INGESTION_ENABLED", "true")
    blocks = [_block(0, "table", "not-a-parseable-markdown-table")]

    transformed_blocks, semantic_parents, semantic_children, warnings = (
        process_semantic_tables_for_pdf(
            blocks=blocks,
            file_name="fallback.pdf",
            file_id="file-fallback",
            artifact_dir=tmp_path,
        )
    )

    assert not warnings
    assert not semantic_parents
    assert not semantic_children
    assert transformed_blocks[0].block_type == "text"
    assert transformed_blocks[0].content == "not-a-parseable-markdown-table"


def test_run_docling_pdf_pipeline_resequences_after_merge(monkeypatch, tmp_path):
    parse_result = DoclingParseResult(
        warnings=[],
        partial_failures=[],
        structured_blocks=[_block(0, "text", "hello")],
    )

    std_parent = ParentChunkModel(
        parent_chunk_id="parent-standard",
        content="standard parent",
        file_metadata={"file_name": "sample.pdf", "file_id": "file-123"},
        parent_chunk_metadata={
            "child_chunks_ids": ["child-standard"],
            "parent_chunk_number": 99,
            "page_number": [1],
            "ingested_at": "now",
        },
        content_flags={"is_image": False, "is_table_image": False},
        artifact_refs={"image_uuid": [], "table_image_uuid": []},
    )
    std_child = ChildChunkModel(
        child_chunk_id="child-standard",
        content="standard child",
        file_metadata={"file_name": "sample.pdf", "file_id": "file-123"},
        child_chunk_metadata={
            "parent_id": "parent-standard",
            "child_chunk_number": 99,
            "page_number": 1,
            "has_preamble": False,
            "ingested_at": "now",
        },
        content_flags={"is_image": False, "is_table_image": False},
        artifact_refs={"image_uuid": None, "table_image_uuid": None},
    )

    semantic_parent = ParentChunkModel(
        parent_chunk_id="parent-semantic",
        content="semantic parent",
        file_metadata={"file_name": "sample.pdf", "file_id": "file-123"},
        parent_chunk_metadata={
            "child_chunks_ids": ["child-semantic"],
            "parent_chunk_number": 99,
            "page_number": [1],
            "ingested_at": "now",
            "table_semantic": {"table_block_index": 3, "group_index": 0},
        },
        content_flags={"is_image": False, "is_table_image": False, "is_semantic_table": True},
        artifact_refs={"image_uuid": [], "table_image_uuid": []},
    )
    semantic_child = ChildChunkModel(
        child_chunk_id="child-semantic",
        content="semantic child",
        file_metadata={"file_name": "sample.pdf", "file_id": "file-123"},
        child_chunk_metadata={
            "parent_id": "parent-semantic",
            "child_chunk_number": 99,
            "page_number": 1,
            "has_preamble": False,
            "ingested_at": "now",
            "table_slice": {"table_block_index": 3, "slice_index": 0},
        },
        content_flags={"is_image": False, "is_table_image": False, "is_semantic_table": True},
        artifact_refs={"image_uuid": None, "table_image_uuid": None},
    )

    monkeypatch.setattr(
        ingest_upload_service.local_artifacts_store,
        "prepare_docling_artifact_dir",
        lambda **_: ("run-id", tmp_path, tmp_path / "document.md"),
    )
    monkeypatch.setattr(
        ingest_upload_service,
        "parse_pdf_with_docling",
        lambda **_: parse_result,
    )
    monkeypatch.setattr(
        ingest_upload_service,
        "process_semantic_tables_for_pdf",
        lambda **_: (
            parse_result.structured_blocks,
            [semantic_parent],
            [semantic_child],
            [],
        ),
    )
    monkeypatch.setattr(
        ingest_upload_service,
        "split_parent_child_chunks_from_docling_blocks",
        lambda **_: ([std_parent], [std_child]),
    )

    parent_dicts, child_dicts, _warnings, _run_id = ingest_upload_service.run_docling_pdf_pipeline(
        file_name="sample.pdf",
        file_bytes=b"%PDF-1.4",
    )

    assert [item["parent_chunk_metadata"]["parent_chunk_number"] for item in parent_dicts] == [0, 1]
    assert [item["child_chunk_metadata"]["child_chunk_number"] for item in child_dicts] == [0, 1]


def test_run_docling_pdf_pipeline_fails_when_semantic_llm_stage_fails(monkeypatch, tmp_path):
    parse_result = DoclingParseResult(
        warnings=[],
        partial_failures=[],
        structured_blocks=[_block(0, "table", "| a | b |\n| --- | --- |\n| 1 | 2 |")],
    )

    monkeypatch.setattr(
        ingest_upload_service.local_artifacts_store,
        "prepare_docling_artifact_dir",
        lambda **_: ("run-id", tmp_path, tmp_path / "document.md"),
    )
    monkeypatch.setattr(
        ingest_upload_service,
        "parse_pdf_with_docling",
        lambda **_: parse_result,
    )
    monkeypatch.setattr(
        ingest_upload_service,
        "process_semantic_tables_for_pdf",
        lambda **_: (_ for _ in ()).throw(
            TableSemanticIngestionError("classifier timeout")
        ),
    )

    with pytest.raises(ingest_upload_service.DoclingChunkingFailedError, match="classifier timeout"):
        ingest_upload_service.run_docling_pdf_pipeline(
            file_name="sample.pdf",
            file_bytes=b"%PDF-1.4",
        )


def test_run_docling_pptexcel_pipeline_does_not_use_pdf_semantic_pipeline(monkeypatch, tmp_path):
    class _FakePptExcelResult:
        def __init__(self):
            self.structured_blocks = [_block(0, "table", "| a | b |\n| --- | --- |\n| 1 | 2 |")]
            self.warnings = []
            self.file_id = "file-office"

    parent = ParentChunkModel(
        parent_chunk_id="parent-office",
        content="office parent",
        file_metadata={"file_name": "sample.xlsx", "file_id": "file-office"},
        parent_chunk_metadata={
            "child_chunks_ids": ["child-office"],
            "parent_chunk_number": 0,
            "page_number": [1],
            "ingested_at": "now",
        },
        content_flags={"is_image": False, "is_table_image": False},
        artifact_refs={"image_uuid": [], "table_image_uuid": []},
    )
    child = ChildChunkModel(
        child_chunk_id="child-office",
        content="office child",
        file_metadata={"file_name": "sample.xlsx", "file_id": "file-office"},
        child_chunk_metadata={
            "parent_id": "parent-office",
            "child_chunk_number": 0,
            "page_number": 1,
            "has_preamble": False,
            "ingested_at": "now",
        },
        content_flags={"is_image": False, "is_table_image": False},
        artifact_refs={"image_uuid": None, "table_image_uuid": None},
    )

    monkeypatch.setattr(
        ingest_upload_service.local_artifacts_store,
        "prepare_docling_artifact_dir",
        lambda **_: ("run-id", tmp_path, tmp_path / "document.md"),
    )
    monkeypatch.setattr(
        ingest_upload_service,
        "parse_pptexcel_with_docling",
        lambda **_: _FakePptExcelResult(),
    )
    monkeypatch.setattr(
        ingest_upload_service,
        "split_parent_child_chunks_from_docling_blocks",
        lambda **_: ([parent], [child]),
    )

    parent_dicts, child_dicts, warnings, _run_id = ingest_upload_service.run_docling_pptexcel_pipeline(
        file_name="sample.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_bytes=b"office-bytes",
    )

    assert len(parent_dicts) == 1
    assert len(child_dicts) == 1
    assert warnings == []
