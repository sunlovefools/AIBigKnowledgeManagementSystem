"""Node 1: retrieval brief extractor."""
from __future__ import annotations

from ..prompts.retrieval_brief_prompts import (
    RETRIEVAL_BRIEF_EXTRACTOR_SYSTEM_PROMPT,
    RETRIEVAL_BRIEF_EXTRACTOR_USER_PROMPT,
)
from ..services import llm_client
from ..shared.normalization import (
    _combine_anchors,
    _fallback_anchors,
    _fallback_goal,
    _fallback_semantic_anchors,
    _normalize_anchors,
    _normalize_constraint,
    _normalize_goal,
    _parse_json_object,
)
from ..state.retrieval_brief_state import RetrievalBriefState


async def retrieval_brief_extractor_node(state: RetrievalBriefState) -> dict:
    """Extract retrieval brief (goal, split anchors, constraint) from user instruction."""
    print("[Agent v2 - Node 1] Extracting retrieval brief...")
    user_instruction = state.get("user_instructions", "")

    fallback_lexical_anchors = _fallback_anchors(user_instruction)
    if not fallback_lexical_anchors:
        fallback_lexical_anchors = ["document"]

    fallback_semantic_anchors = _fallback_semantic_anchors(user_instruction)
    if not fallback_semantic_anchors:
        fallback_semantic_anchors = fallback_lexical_anchors[:]

    fallback = {
        "goal": _fallback_goal(user_instruction),
        "lexical_anchors": fallback_lexical_anchors,
        "semantic_anchors": fallback_semantic_anchors,
        "anchors": _combine_anchors(fallback_lexical_anchors, fallback_semantic_anchors),
        "constraint": "None",
    }

    try:
        llm_text, usage = await llm_client._call_llm(
            system_prompt=RETRIEVAL_BRIEF_EXTRACTOR_SYSTEM_PROMPT,
            user_message=RETRIEVAL_BRIEF_EXTRACTOR_USER_PROMPT.format(
                user_instruction=user_instruction
            ),
            session=state.get("_session"),
            run_id=state.get("run_id"),
            step="retrieval_brief_extractor",
            max_tokens=512,
        )
        parsed = _parse_json_object(llm_text)

        goal = _normalize_goal(parsed.get("goal"), user_instruction)

        lexical_anchors = _normalize_anchors(parsed.get("lexical_anchors"))
        semantic_anchors = _normalize_anchors(parsed.get("semantic_anchors"))

        legacy_anchors = _normalize_anchors(parsed.get("anchors"))
        if not lexical_anchors and legacy_anchors:
            lexical_anchors = legacy_anchors
        if not semantic_anchors and legacy_anchors:
            semantic_anchors = legacy_anchors

        if not lexical_anchors:
            lexical_anchors = fallback_lexical_anchors
        if not semantic_anchors:
            semantic_anchors = fallback_semantic_anchors

        anchors = _combine_anchors(lexical_anchors, semantic_anchors)
        constraint = _normalize_constraint(parsed.get("constraint"))

        return {
            "goal": goal,
            "lexical_anchors": lexical_anchors,
            "semantic_anchors": semantic_anchors,
            "anchors": anchors,
            "constraint": constraint,
            **llm_client._accumulate_usage(state, usage),
        }
    except Exception as error:
        print(f"Retrieval brief extraction failed: {error}. Falling back.")
        return fallback

