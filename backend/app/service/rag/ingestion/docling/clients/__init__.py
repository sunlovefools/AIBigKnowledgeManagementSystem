"""
Docling backend clients package.
"""

from .beam_client import build_beam_layout
from .beam_client import parse_pdf_with_docling as parse_pdf_with_docling_beam
from .local_client import build_local_layout
from .local_client import parse_pdf_with_docling_local

__all__ = [
    "build_beam_layout",
    "build_local_layout",
    "parse_pdf_with_docling_beam",
    "parse_pdf_with_docling_local",
]
