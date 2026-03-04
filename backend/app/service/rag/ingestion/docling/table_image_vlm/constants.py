# Module purpose:
# Centralizes constants and regex patterns used across the table-image VLM pipeline,
# including artifact naming, defaults, and OpenRouter model/endpoint settings.

import os
import re


# Shared constants for the table-image VLM enrichment pipeline.
# These are reused by both Beam and local Docling backends.
TABLE_IMAGE_VLM_OUTPUT_DIRNAME = "table_image_vlm"  # Output for both JSON and summary artifacts from the VLM processing of fallback table images.
TABLE_IMAGE_VLM_DEFAULT_CONTEXT_BLOCKS = 3  # Number of surrounding markdown blocks to send before/after the table.
TABLE_IMAGE_VLM_DEFAULT_AFTER_READY_BLOCKS = 3  # Submit only after this many blocks exist after the table (unless force=True).
TABLE_IMAGE_VLM_DEFAULT_MAX_WORKERS = 4
TABLE_IMAGE_VLM_DEFAULT_MODEL = os.getenv("VISION_LM_MODEL")
TABLE_IMAGE_VLM_DEFAULT_OPENROUTER_URL = os.getenv("OPENROUTER_URL")
_MARKDOWN_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_MARKDOWN_IMAGE_LINE_RE = re.compile(r"^\s*>?\s*!\[.*?\]\(.*?\)\s*$")

# Embedded OpenRouter helper config (migrated from `image_processing/openrouter_extract_table.py`)
MAX_TOKENS = 4000
MODEL = TABLE_IMAGE_VLM_DEFAULT_MODEL or "qwen/qwen3-vl-30b-a3b-thinking"
OPENROUTER_URL = (
    TABLE_IMAGE_VLM_DEFAULT_OPENROUTER_URL
    or "https://openrouter.ai/api/v1/chat/completions"
)
