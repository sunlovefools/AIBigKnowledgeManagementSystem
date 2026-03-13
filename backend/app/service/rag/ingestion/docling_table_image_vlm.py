"""Compatibility shim for legacy import path."""

import sys as _sys
from app.service.rag.ingestion.docling import table_image_vlm as _impl

_sys.modules[__name__] = _impl
