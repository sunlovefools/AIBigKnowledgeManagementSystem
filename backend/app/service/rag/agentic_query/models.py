"""Typed contracts used by the agentic query runtime.

These models provide two guardrails:
1. They validate each LLM-issued tool action before execution.
2. They define the normalized result returned by the runtime.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentAction(BaseModel):
    """One model-issued action in the JSON tool protocol."""

    action: Literal[
        "load_answering_instructions",
        "find_files_by_name",
        "find_inventory_records",
        "search_relevant_chunks",
        "read_chunk_detail",
        "read_file_chunks",
        "read_skill_reference",
        "provide_final_answer",
        # Backward-compatible aliases accepted from older prompts/tests.
        "load_skill",
        "search_files",
        "find_module_overviews",
        "search_context",
        "fetch_parent_chunk",
        "fetch_file_context",
        "read_reference",
        "finish",
    ]
    # Arguments are validated again by per-action argument models.
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Short operational action summary shown in debug/progress trace.
    intent: str | None = None
    success_criteria: str | None = None
    fallback: str | None = None
    # Backward-compatible one-line fallback used by earlier prompt versions.
    decision: str | None = None


class LoadSkillArguments(BaseModel):
    """Arguments for loading one skill body."""

    skill_name: str


class SearchContextArguments(BaseModel):
    """Arguments for a scoped retrieval lookup."""

    query: str
    top_k: int = 8


class SearchFilesArguments(BaseModel):
    """Arguments for finding scoped files by filename."""

    query: str
    limit: int = 5


class FindInventoryRecordsArguments(BaseModel):
    """Arguments for deterministic scoped inventory discovery."""

    query: str
    max_matches: int = 50


class FetchParentChunkArguments(BaseModel):
    """Arguments for loading one specific parent chunk."""

    parent_id: str


class FetchFileContextArguments(BaseModel):
    """Arguments for loading ordered parent chunks from one scoped file."""

    file_id: str | None = None
    file_name: str | None = None
    max_chunks: int = 20


class ReadReferenceArguments(BaseModel):
    """Arguments for loading one optional markdown reference document."""

    skill_name: str
    ref_id: str


class FinishArguments(BaseModel):
    """Arguments for finalizing the run with answer + file-level citations."""

    answer: str
    citations: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    """Compact evidence payload surfaced to the agent loop and frontend."""

    parent_id: str
    file_id: str
    file_name: str
    parent_chunk_number: int | None = None
    snippet: str
    structured_view: str = ""


class FileMatch(BaseModel):
    """Compact scoped file search result."""

    file_id: str
    file_name: str
    first_parent_id: str | None = None
    preview: str = ""


class AgenticQueryRunResult(BaseModel):
    """Terminal output emitted by `run_agentic_query`.

    `termination_reason` is one of:
    - `finished`
    - `forced_finish_after_max_steps`
    - `max_steps_exceeded`
    - `timeout`
    """

    answer: str
    citations: list[str] = Field(default_factory=list)
    run_id: str
    termination_reason: str
    tool_call_count: int = 0
