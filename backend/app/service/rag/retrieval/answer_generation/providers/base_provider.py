"""Provider interface for answer generation implementations.

This module defines the contract each provider must satisfy so orchestration can
route requests uniformly. It should not include provider-specific implementation logic.
"""

from __future__ import annotations

from typing import Protocol

import aiohttp

from ..models import AnswerGeneratorConfig, NormalizedRagDoc


class AnswerProvider(Protocol):
    """Protocol for answer generation provider implementations."""

    async def generate(
        self,
        session: aiohttp.ClientSession,
        cfg: AnswerGeneratorConfig,
        rag_docs: list[NormalizedRagDoc],
        user_query: str,
    ) -> str:
        """Generate an answer string for a user query and normalized context."""
        ...
