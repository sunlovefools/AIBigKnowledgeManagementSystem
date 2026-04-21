"""Markdown-first config loading for agentic query.

Design intent:
- Keep policy/instructions in markdown (`skills.md`).
- Keep execution/safety in Python runtime code.
- Load reference markdown files lazily (only when tool calls request them).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class AgenticQueryConfig:
    """In-memory runtime config resolved from the `config/` directory."""

    system_prompt: str
    reference_paths: dict[str, Path]
    skills_path: Path
    reference_root: Path


_REQUIRED_ACTION_NAMES = {
    "search_context",
    "fetch_parent_chunk",
    "read_reference",
    "finish",
}


def _config_root() -> Path:
    """Return the local config folder beside this module."""

    return Path(__file__).resolve().parent / "config"


def _read_text(path: Path) -> str:
    """Read UTF-8 text with graceful fallback for imperfect files."""

    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _validate_skills_markdown(skills_text: str) -> None:
    """Fail fast if `skills.md` is empty or missing required action names."""

    normalized = str(skills_text or "")
    if not normalized.strip():
        raise RuntimeError("agentic_query skills.md is empty.")

    missing_actions = [
        action_name
        for action_name in sorted(_REQUIRED_ACTION_NAMES)
        if action_name not in normalized
    ]
    if missing_actions:
        raise RuntimeError(
            "agentic_query skills.md is missing required action names: "
            + ", ".join(missing_actions)
        )


def _build_reference_index(reference_dir: Path) -> dict[str, Path]:
    """Build `{ref_id -> file_path}` index from `references/*.md`."""

    if not reference_dir.exists() or not reference_dir.is_dir():
        return {}

    index: dict[str, Path] = {}
    for path in sorted(reference_dir.glob("*.md")):
        ref_id = str(path.stem or "").strip().lower()
        if not ref_id:
            continue
        index[ref_id] = path
    return index


@lru_cache(maxsize=1)
def load_agentic_query_config() -> AgenticQueryConfig:
    """Load and validate markdown config once per process."""

    root = _config_root()
    skills_path = root / "skills.md"
    if not skills_path.exists():
        raise RuntimeError(f"Missing agentic_query skills file: {skills_path}")

    skills_text = _read_text(skills_path)
    _validate_skills_markdown(skills_text)
    reference_paths = _build_reference_index(root / "references")
    return AgenticQueryConfig(
        system_prompt=skills_text,
        reference_paths=reference_paths,
        skills_path=skills_path,
        reference_root=root / "references",
    )


def read_reference_content(
    config: AgenticQueryConfig,
    ref_id: str,
    *,
    max_chars: int = 3000,
) -> str:
    """Resolve one reference by `ref_id` and return bounded content text."""

    normalized_ref_id = str(ref_id or "").strip().lower()
    if not normalized_ref_id:
        raise ValueError("ref_id must be a non-empty string.")

    path = config.reference_paths.get(normalized_ref_id)
    if path is None:
        raise KeyError(f"Unknown reference id: {normalized_ref_id}")

    text = _read_text(path).strip()
    if max_chars <= 0:
        return text
    return text[:max_chars]
