"""Normalization and fallback helpers for Agentic Modification."""
from __future__ import annotations

import json
import re
from typing import Any

# Remove common words that are unlikely to be useful as anchors
_STOPWORDS = {
    "a",
    "an",
    "and",
    "all",
    "any",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "under",
    "update",
    "change",
    "modify",
    "replace",
    "remove",
    "set",
    "make",
}


def _clean_llm_output(text: str) -> str:
    """
    Clean LLM output by removing code block wrappers and extraneous whitespace.
    """
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2:
            if lines[-1].strip().startswith("```"):
                cleaned = "\n".join(lines[1:-1]).strip()
            else:
                cleaned = "\n".join(lines[1:]).strip()
    return cleaned


def _parse_json_object(text: str) -> dict[str, Any]:
    """Parse JSON object from model output, recovering from wrapper text if possible."""
    cleaned = _clean_llm_output(text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        snippet = cleaned[start : end + 1]
        parsed = json.loads(snippet)
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Failed to parse retrieval brief JSON object.")


def _fallback_goal(user_instruction: str) -> str:
    """
    Generate a fallback goal based on the user instruction if the model fails to produce a valid one.
    """
    instruction = re.sub(r"\s+", " ", (user_instruction or "").strip())
    if not instruction:
        return "Update content based on user instruction."
    if len(instruction) > 140:
        instruction = instruction[:137].rstrip() + "..."
    if not instruction.endswith("."):
        instruction += "."
    return instruction


def _fallback_anchors(user_instruction: str) -> list[str]:
    """
    Generate fallback anchors by extracting quoted phrases, numeric expressions, entities, and keywords from the user instruction.
    """
    #TODO: I dont think we need this fallback (Straight away go to search/group with the goal as the instruction)
    instruction = user_instruction or ""
    anchors: list[str] = []

    quoted_phrases = re.findall(r'"([^"]+)"|\'([^\']+)\'', instruction)
    for pair in quoted_phrases:
        phrase = (pair[0] or pair[1]).strip()
        if phrase:
            anchors.append(phrase)

    numeric_phrases = re.findall(
        r"\b\d+(?:\.\d+)?\s*(?:days?|weeks?|months?|years?|hours?|minutes?|%|percent|usd|eur|gbp|dollars?)\b",
        instruction,
        flags=re.IGNORECASE,
    )
    anchors.extend(numeric_phrases)

    entities = re.findall(r"\b[A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*)*\b", instruction)
    anchors.extend(entities)

    keywords = re.findall(r"\b[a-zA-Z][a-zA-Z0-9-]{2,}\b", instruction.lower())
    for word in keywords:
        if word not in _STOPWORDS:
            anchors.append(word)

    return _normalize_anchors(anchors)[:5]


def _fallback_semantic_anchors(user_instruction: str) -> list[str]:
    """
    Fallback Semantic anchors can use the user instruction as a single anchor
    """
    return _fallback_anchors(user_instruction)


def _normalize_goal(raw_goal: Any, user_instruction: str) -> str:
    """
    Normalize the goal by stripping whitespace, collapsing internal spaces, truncating if too long, and ensuring it ends with a period. 
    If the raw goal is empty or invalid, generate a fallback goal from the user instruction."""
    goal = str(raw_goal).strip() if raw_goal is not None else ""
    if not goal:
        goal = _fallback_goal(user_instruction)
    goal = re.sub(r"\s+", " ", goal).strip()
    if len(goal) > 180:
        goal = goal[:177].rstrip() + "..."
    if not goal.endswith("."):
        goal += "."
    return goal


def _normalize_anchors(raw_anchors: Any) -> list[str]:
    """
    Normalize anchors by ensuring they are a list of unique, cleaned strings.
    """
    if isinstance(raw_anchors, list):
        candidates = raw_anchors
    elif isinstance(raw_anchors, str):
        candidates = [raw_anchors]
    else:
        candidates = []

    normalized: list[str] = []
    seen: set[str] = set()

    for item in candidates:
        if not isinstance(item, str):
            continue
        anchor = re.sub(r"\s+", " ", item).strip().strip(",.;:")
        if not anchor:
            continue
        key = anchor.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(anchor)

    return normalized


def _combine_anchors(lexical_anchors: list[str], semantic_anchors: list[str]) -> list[str]:
    """
    Combine lexical and semantic anchors, prioritizing uniqueness and cleanliness.
    """
    return _normalize_anchors([*lexical_anchors, *semantic_anchors])


def _normalize_constraint(raw_constraint: Any) -> str:
    """
    Normalize the constraint by stripping whitespace and checking for common "none" indicators.
    """
    constraint = str(raw_constraint).strip() if raw_constraint is not None else ""
    if not constraint:
        return "None"
    lowered = constraint.casefold()
    if lowered in {"none", "null", "n/a", "na", "no constraint"}:
        return "None"
    constraint = re.sub(r"\s+", " ", constraint).strip()
    return constraint if constraint else "None"


def _normalize_excluded_file_ids(raw_excluded_file_ids: Any) -> set[str]:
    """
    Normalize excluded file IDs by ensuring they are a set of unique, cleaned strings.
    """
    if isinstance(raw_excluded_file_ids, set):
        candidates = list(raw_excluded_file_ids)
    elif isinstance(raw_excluded_file_ids, list):
        candidates = raw_excluded_file_ids
    elif isinstance(raw_excluded_file_ids, tuple):
        candidates = list(raw_excluded_file_ids)
    else:
        candidates = []

    normalized: set[str] = set()
    for item in candidates:
        value = str(item or "").strip()
        if value:
            normalized.add(value)
    return normalized

