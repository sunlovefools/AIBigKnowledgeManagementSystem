"""Typed contracts for the Skills-style modification runtime."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class AgentAction(BaseModel):
    action: Literal[
        "load_skill",
        "search_files",
        "search_context",
        "fetch_file_outline",
        "fetch_parent_chunk",
        "fetch_chunk_window",
        "delegate_file_edits",
        "read_reference",
        "finish",
    ]
    arguments: dict[str, Any] = Field(default_factory=dict)
    intent: str | None = None
    success_criteria: str | None = None
    fallback: str | None = None
    decision: str | None = None


class LoadSkillArguments(BaseModel):
    skill_name: str


class SearchFilesArguments(BaseModel):
    query: str
    limit: int = 5


class SearchContextArguments(BaseModel):
    query: str
    top_k: int = 8


class FetchFileOutlineArguments(BaseModel):
    file_id: str | None = None
    file_name: str | None = None
    max_chunks: int = 80


class FetchParentChunkArguments(BaseModel):
    parent_id: str


class FetchChunkWindowArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_id: str | None = Field(default=None, validation_alias=AliasChoices("file_id", "fileId"))
    parent_id: str | None = Field(default=None, validation_alias=AliasChoices("parent_id", "parentId"))
    center_parent_id: str | None = Field(default=None, validation_alias=AliasChoices("center_parent_id", "centerParentId"))
    center_chunk_number: int | None = None
    before: int = 1
    after: int = 1
    window_size: int | None = Field(default=None, validation_alias=AliasChoices("window_size", "windowSize"))

    @model_validator(mode="after")
    def normalize_aliases(self) -> "FetchChunkWindowArguments":
        if self.center_parent_id is None and self.parent_id:
            self.center_parent_id = self.parent_id
        if self.window_size is not None:
            radius = max(0, int(self.window_size))
            self.before = radius
            self.after = radius
        return self


class DelegateFileEditsArguments(BaseModel):
    file_ids: list[str]
    instruction: str | None = None


class ReadReferenceArguments(BaseModel):
    skill_name: str
    ref_id: str


class SkippedCandidate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_id: str = Field(validation_alias=AliasChoices("file_id", "fileId"))
    file_name: str | None = Field(default=None, validation_alias=AliasChoices("file_name", "fileName"))
    reason: str


class ProposalItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    fileId: str = Field(validation_alias=AliasChoices("fileId", "file_id"))
    fileName: str = Field(validation_alias=AliasChoices("fileName", "file_name"))
    parentId: str = Field(validation_alias=AliasChoices("parentId", "parent_id"))
    original: str
    proposed: str
    source: Literal["agent"] = "agent"


class FinishArguments(BaseModel):
    proposals: list[ProposalItem] = Field(default_factory=list)
    skipped_candidates: list[SkippedCandidate] = Field(default_factory=list)
    summary: str | None = None


class EvidenceItem(BaseModel):
    parent_id: str
    file_id: str
    file_name: str
    parent_chunk_number: int | None = None
    snippet: str


class FileMatch(BaseModel):
    file_id: str
    file_name: str
    first_parent_id: str | None = None
    preview: str = ""


class FileOutlineChunk(BaseModel):
    parent_id: str
    file_id: str
    file_name: str
    parent_chunk_number: int | None = None
    heading: str | None = None
    preview: str
    size: int


class ParentChunk(BaseModel):
    parent_id: str
    file_id: str
    file_name: str
    parent_chunk_number: int | None = None
    content: str


class ChunkWindow(BaseModel):
    file_id: str
    file_name: str
    chunks: list[ParentChunk] = Field(default_factory=list)


class FileWorkerResult(BaseModel):
    file_id: str
    file_name: str
    proposals: list[ProposalItem] = Field(default_factory=list)
    explored_parent_ids: list[str] = Field(default_factory=list)
    skipped: bool = False
    reason: str | None = None


class AgenticModificationSkillRunResult(BaseModel):
    intention: str = "edit"
    proposals: list[ProposalItem] = Field(default_factory=list)
    goal: str = ""
    lexical_anchors: list[str] = Field(default_factory=list)
    semantic_anchors: list[str] = Field(default_factory=list)
    anchors: list[str] = Field(default_factory=list)
    constraint: str = "None"
    skill_runtime_result: dict[str, Any] = Field(default_factory=dict)
    coverage_report: dict[str, Any] = Field(default_factory=dict)
    run_id: str
    termination_reason: str
    tool_call_count: int = 0
    token_prompt_total: int = 0
    token_completion_total: int = 0
    token_total: int = 0
    llm_call_count: int = 0
