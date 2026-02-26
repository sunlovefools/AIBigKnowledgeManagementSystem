# Module purpose:
# Re-exports the table-image VLM package public API so extractors can import a single
# module for runtime setup, VLM calls, job orchestration, and artifact/path helpers.

from .artifacts import (
    _prepare_table_image_vlm_worker_paths,
    _table_image_vlm_dir_name,
    _write_json_file,
    _write_text_file,
    table_image_vlm_json_rel_path,
    table_image_vlm_output_dir,
    table_image_vlm_summary_placeholder,
)
from .constants import (
    MAX_TOKENS,
    MODEL,
    OPENROUTER_URL,
    TABLE_IMAGE_VLM_DEFAULT_AFTER_READY_BLOCKS,
    TABLE_IMAGE_VLM_DEFAULT_CONTEXT_BLOCKS,
    TABLE_IMAGE_VLM_DEFAULT_MAX_WORKERS,
    TABLE_IMAGE_VLM_DEFAULT_MODEL,
    TABLE_IMAGE_VLM_DEFAULT_OPENROUTER_URL,
    TABLE_IMAGE_VLM_OUTPUT_DIRNAME,
    _MARKDOWN_COMMENT_RE,
    _MARKDOWN_IMAGE_LINE_RE,
)
from .context import _is_text_like_context_block, _normalize_markdown_context_for_vlm
from .jobs import (
    _build_table_image_vlm_summary_replacement,
    _process_table_image_vlm_job,
    finalize_table_image_vlm_jobs,
    submit_ready_table_image_vlm_jobs,
)
from .models import TableImageVlmJob, TableImageVlmRuntime, TableImageVlmWorkerResult
from .openrouter import (
    _call_openrouter_messages,
    _encode_image_to_data_url,
    _extract_json_from_content,
    _get_message_content,
    extract_table_json_from_image,
    extract_table_semantic_summary_from_image,
)
from .prompts import (
    SEMANTIC_SUMMARY_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
    _build_semantic_summary_prompt,
)
from .runtime import (
    _load_table_image_vlm_helper_module,
    _parse_bool_env,
    _parse_positive_int_env,
    build_table_image_vlm_runtime,
)


build_table_image_vlm_runtime_config = build_table_image_vlm_runtime


__all__ = [
    "TABLE_IMAGE_VLM_OUTPUT_DIRNAME",
    "TableImageVlmJob",
    "TableImageVlmRuntime",
    "TableImageVlmWorkerResult",
    "build_table_image_vlm_runtime",
    "build_table_image_vlm_runtime_config",
    "extract_table_json_from_image",
    "extract_table_semantic_summary_from_image",
    "finalize_table_image_vlm_jobs",
    "submit_ready_table_image_vlm_jobs",
    "table_image_vlm_json_rel_path",
    "table_image_vlm_output_dir",
    "table_image_vlm_summary_placeholder",
]
