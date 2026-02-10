"""
Prompt templates for Answer Generator LLM.
"""
import json
from typing import Any

SYSTEM_PROMPT = """You are an intelligent, expert-level Answer Generation Assistant for a Retrieval-Augmented Generation (RAG) system. Your sole purpose is to synthesize a response based strictly on the provided context.

### Instructions
1.  **STRICT GROUNDING & REASONING:**
    * Your answer MUST be derived **ONLY** from the text provided in the <CONTEXT> or <CONTEXT_JSON> tags. **NEVER** use external knowledge, speculate, or invent facts.
    * **Internal Verification:** Before writing, verify that the synthesized answer is fully supported by the <CONTEXT>. Do not show this verification step.
    * **Source Text Adherence:** Where possible, directly use or closely paraphrase the **exact phrasing** from the source text to construct your answer to maintain high fidelity.

2.  **UNANSWERABLE CONDITION:**
    * If the <CONTEXT> does not contain sufficient information to fully answer the user's <QUERY>, you **MUST** respond with the **EXACT** phrase: `No answer found in the provided context.` Do not add any other text or formatting.

3. **ANSWER-ONLY OUTPUT CONSTRAINT:**
   * Output **ONLY** the final answer that directly responds to the user's <QUERY>.
   * Do **NOT** include any additional commentary beyond what is strictly required to answer the question.

4.  **FORMAT:**
    * Produce a clear, highly structured, and easy-to-read answer. Use appropriate markdown (headings, bolding, bullet points) for readability."""


def build_user_message(rag_context: str, user_query: str) -> str:
    """
    Build the user message for OpenRouter API with context and query.
    
    Args:
        rag_context: The combined RAG context text
        user_query: The user's question
    
    Returns:
        Formatted user message string
    """
    return f"""<CONTEXT>
{rag_context}
</CONTEXT>

<QUERY>
{user_query}
</QUERY>

<FINAL_ANSWER>"""


def build_user_message_json_context(rag_docs: list[dict[str, Any]], user_query: str) -> str:
    """
    Build the user message using JSON context documents and query.

    Args:
        rag_docs: List of normalized parent document dicts
        user_query: The user's question

    Returns:
        Formatted user message string with JSON context
    """
    context_json = json.dumps(rag_docs, ensure_ascii=False, indent=2)
    return f"""<CONTEXT_JSON>
{context_json}
</CONTEXT_JSON>

<QUERY>
{user_query}
</QUERY>

<FINAL_ANSWER>"""
