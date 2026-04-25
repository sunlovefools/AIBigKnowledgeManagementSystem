"""Progressive-disclosure config loading for the Skills modification runtime."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    path: Path
    reference_root: Path
    reference_ids: tuple[str, ...]

    def as_registry_item(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "allowed_tools": list(self.allowed_tools),
            "references": list(self.reference_ids),
        }


@dataclass(frozen=True)
class AgenticModificationSkillConfig:
    system_prompt: str
    system_path: Path
    skill_registry: dict[str, SkillMetadata]
    skill_paths: dict[str, Path]
    reference_paths: dict[str, dict[str, Path]]
    config_root: Path


def _config_root() -> Path:
    return Path(__file__).resolve().parent / "config"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _normalize_skill_name(raw: str) -> str:
    return str(raw or "").strip().lower().replace("_", "-")


def _parse_scalar(raw: str) -> str:
    value = str(raw or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_frontmatter(markdown_text: str) -> tuple[dict[str, Any], str]:
    text = str(markdown_text or "")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()

    end_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, text.strip()

    metadata: dict[str, Any] = {}
    current_list_key: str | None = None
    for line in lines[1:end_index]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-") and current_list_key:
            item = _parse_scalar(stripped[1:].strip())
            if item:
                metadata.setdefault(current_list_key, []).append(item)
            continue
        current_list_key = None
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value:
            metadata[key] = _parse_scalar(value)
        else:
            metadata[key] = []
            current_list_key = key

    return metadata, "\n".join(lines[end_index + 1 :]).strip()


def _build_reference_index(reference_dir: Path) -> dict[str, Path]:
    if not reference_dir.exists() or not reference_dir.is_dir():
        return {}
    return {
        str(path.stem or "").strip().lower(): path
        for path in sorted(reference_dir.glob("*.md"))
        if str(path.stem or "").strip()
    }


def _build_skill_registry(skills_root: Path) -> tuple[
    dict[str, SkillMetadata],
    dict[str, Path],
    dict[str, dict[str, Path]],
]:
    registry: dict[str, SkillMetadata] = {}
    skill_paths: dict[str, Path] = {}
    reference_paths: dict[str, dict[str, Path]] = {}

    if not skills_root.exists() or not skills_root.is_dir():
        return registry, skill_paths, reference_paths

    for skill_file in sorted(skills_root.glob("**/SKILL.md")):
        frontmatter, _body = _parse_frontmatter(_read_text(skill_file))
        skill_name = _normalize_skill_name(str(frontmatter.get("name") or skill_file.parent.name))
        if not skill_name:
            continue

        allowed_tools_raw = frontmatter.get("allowed-tools") or frontmatter.get("allowed_tools") or []
        if isinstance(allowed_tools_raw, str):
            allowed_tools = (allowed_tools_raw,)
        elif isinstance(allowed_tools_raw, list):
            allowed_tools = tuple(str(item).strip() for item in allowed_tools_raw if str(item).strip())
        else:
            allowed_tools = ()

        reference_root = skill_file.parent / "references"
        refs = _build_reference_index(reference_root)
        metadata = SkillMetadata(
            name=skill_name,
            description=str(frontmatter.get("description") or "").strip(),
            allowed_tools=allowed_tools,
            path=skill_file,
            reference_root=reference_root,
            reference_ids=tuple(sorted(refs.keys())),
        )
        registry[skill_name] = metadata
        skill_paths[skill_name] = skill_file
        reference_paths[skill_name] = refs

    return registry, skill_paths, reference_paths


@lru_cache(maxsize=1)
def load_agentic_modification_skill_config() -> AgenticModificationSkillConfig:
    root = _config_root()
    system_path = root / "system.md"
    if not system_path.exists():
        raise RuntimeError(f"Missing modification skill system prompt file: {system_path}")

    system_prompt = _read_text(system_path).strip()
    if not system_prompt:
        raise RuntimeError("agentic_modification_skill config/system.md is empty.")

    registry, skill_paths, reference_paths = _build_skill_registry(root / "skills")
    if not registry:
        raise RuntimeError(f"No modification skills found under: {root / 'skills'}")

    return AgenticModificationSkillConfig(
        system_prompt=system_prompt,
        system_path=system_path,
        skill_registry=registry,
        skill_paths=skill_paths,
        reference_paths=reference_paths,
        config_root=root,
    )


def load_skill_content(
    config: AgenticModificationSkillConfig,
    skill_name: str,
    *,
    max_chars: int = 8000,
) -> dict[str, Any]:
    normalized = _normalize_skill_name(skill_name)
    path = config.skill_paths.get(normalized)
    metadata = config.skill_registry.get(normalized)
    if path is None or metadata is None:
        raise KeyError(f"Unknown skill: {normalized}")

    frontmatter, body = _parse_frontmatter(_read_text(path))
    return {
        "skill_name": normalized,
        "frontmatter": frontmatter,
        "body": body[:max_chars] if max_chars > 0 else body,
        "path": str(path),
        "references": list(metadata.reference_ids),
    }


def read_reference_content(
    config: AgenticModificationSkillConfig,
    skill_name: str,
    ref_id: str,
    *,
    max_chars: int = 3000,
) -> str:
    normalized_skill = _normalize_skill_name(skill_name)
    normalized_ref = str(ref_id or "").strip().lower()
    skill_refs = config.reference_paths.get(normalized_skill)
    if skill_refs is None:
        raise KeyError(f"Unknown skill: {normalized_skill}")
    path = skill_refs.get(normalized_ref)
    if path is None:
        raise KeyError(f"Unknown reference id for {normalized_skill}: {normalized_ref}")
    text = _read_text(path).strip()
    return text[:max_chars] if max_chars > 0 else text
