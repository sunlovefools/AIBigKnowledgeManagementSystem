"""Shared data models for the answer generation package.

This module defines typed contracts used across config, providers, and orchestration.
It exists to keep data shapes explicit and reusable. It should not perform any I/O,
network access, or environment loading.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict

# Provider names supported by the answer generation orchestrator.
ProviderName = Literal["OLLAMA", "BEAM", "OPENROUTER"]

# Global defaults reused by config and providers.
OPENROUTER_URL_DEFAULT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT_S = 500.0
NO_ANSWER_FALLBACK = "No answer returned by Answer Generator"
SOURCES_SUFFIX_UNKNOWN = "(Sources: filename unknown)"


class NormalizedMetadata(TypedDict, total=False):
    """Minimal metadata retained for provenance and citation support."""

    file_name: str
    parent_chunk_number: int


class NormalizedRagDoc(TypedDict):
    """Normalized RAG document payload shape used by provider implementations."""

    id: Any | None
    metadata: NormalizedMetadata
    page_content: str
    type: str


@dataclass(frozen=True)
class AnswerGeneratorConfig:
    """Runtime provider configuration resolved from environment variables."""

    provider: str
    timeout_s: float
    url: str | None
    model: str | None
    api_key: str | None
    ollama_swap_to_ram: bool = False


@dataclass(frozen=True)
class GenerationRequest:
    """Input bundle for answer generation requests in provider modules."""

    rag_docs: list[NormalizedRagDoc]
    user_query: str


@dataclass(frozen=True)
class GenerationResult:
    """Output envelope for future observability/extensions of generation flows."""

    answer: str
    provider: str
    model: str | None
