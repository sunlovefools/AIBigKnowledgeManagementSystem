"""
Semantic table ingestion package.
"""

from .pipeline import (
    TableSemanticIngestionError,
    process_semantic_tables_for_pdf,
)

__all__ = [
    "TableSemanticIngestionError",
    "process_semantic_tables_for_pdf",
]

