"""Node 1: retrieval brief extractor.

This node converts a free-form user edit request into structured retrieval intent:
- `goal`: concise description of the desired change
- `lexical_anchors` / `semantic_anchors`: retrieval signals for downstream nodes
- `constraint`: scope guard used by Node 4 and Node 5
"""
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
from ..shared.progress import emit_progress
from ..state.retrieval_brief_state import RetrievalBriefState


async def retrieval_brief_extractor_node(state: RetrievalBriefState) -> dict:
    """
    Entry point of Node 1: Retrieval Brief Extractor.
    
    This node takes the user's modification request and extracts a structured retrieval brief
    using an LLM. The retrieval brief includes the modification goal, lexical and semantic anchors,
    and any constraints on what can be modified. The output is normalized and includes fallbacks
    to ensure robustness against LLM failures or unexpected outputs.
    """

    # Node 1 is the only stage that reads raw user instructions directly.
    print("[Agentic Modification - Node 1] Extracting retrieval brief...")
    # Emit concrete stage status so UI can show true backend progress.
    await emit_progress(
        state,
        stage="retrieval_brief_extractor",
        status="started",
        message="Extracting retrieval brief from the user instruction.",
    )
    user_instruction = state.get("user_instructions", "")

    #TODO: I think we can remove this in the future once we have more confidence in the LLM performance, or we can keep it as a lightweight safety net.
    # Generate fallback lexical anchors and semantic anchors in case LLM call fails or returns invalid output
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

    # Primary path: let the LLM build a structured brief from the raw request.
    try:
        llm_text, usage = await llm_client._call_llm(
            system_prompt=RETRIEVAL_BRIEF_EXTRACTOR_SYSTEM_PROMPT, 
            #TODO: The prompts can be put into another module for better organization 
            user_message=RETRIEVAL_BRIEF_EXTRACTOR_USER_PROMPT.format(
                user_instruction=user_instruction
            ),
            session=state.get("_session"),
            run_id=state.get("run_id"),
            step="retrieval_brief_extractor",
            max_tokens=512,
        )

        # The return output from the LLM should be a JSON object
        parsed = _parse_json_object(llm_text)

        # Get the goal, anchors and constraint from the parsed JSON object, and apply normalization
        goal = _normalize_goal(parsed.get("goal"), user_instruction)
        constraint = _normalize_constraint(parsed.get("constraint"))
        lexical_anchors = _normalize_anchors(parsed.get("lexical_anchors"))
        semantic_anchors = _normalize_anchors(parsed.get("semantic_anchors"))

        # Fill any missing anchors so Node 2 always has at least one retrieval signal.
        if not lexical_anchors:
            lexical_anchors = fallback_lexical_anchors
        if not semantic_anchors and fallback_semantic_anchors:
            semantic_anchors = fallback_semantic_anchors

        if not lexical_anchors:
            lexical_anchors = fallback_lexical_anchors
        if not semantic_anchors:
            semantic_anchors = fallback_semantic_anchors

        anchors = _combine_anchors(lexical_anchors, semantic_anchors)
        # Include lightweight counts for debugging in frontend progress consumers.
        await emit_progress(
            state,
            stage="retrieval_brief_extractor",
            status="completed",
            message="Retrieval brief extracted.",
            metadata={
                "lexicalAnchorCount": len(lexical_anchors),
                "semanticAnchorCount": len(semantic_anchors),
            },
        )

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
        # Surface failure before fallback so UI does not look stalled.
        await emit_progress(
            state,
            stage="retrieval_brief_extractor",
            status="failed",
            message="Retrieval brief extraction failed. Falling back to heuristic anchors.",
            metadata={"error": str(error)},
        )
        return fallback
