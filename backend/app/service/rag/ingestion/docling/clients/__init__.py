"""
Docling backend clients package.
"""

from .beam_client import build_beam_layout
from .local_client import build_local_layout

__all__ = [
    "build_beam_layout",
    "build_local_layout",
]
