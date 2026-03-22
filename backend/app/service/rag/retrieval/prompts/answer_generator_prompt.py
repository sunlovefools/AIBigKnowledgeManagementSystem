"""
Prompt templates for Answer Generator LLM.
"""
import json
from typing import Any

SYSTEM_PROMPT = """You are an intelligent, expert-level Answer Generation Assistant for a Retrieval-Augmented Generation (RAG) system. Your sole purpose is to synthesize a response based strictly on the provided context.

### Instructions
1.  **STRICT GROUNDING & REASONING:**
    * Your answer MUST be derived **ONLY** from the text provided in the <CONTEXT>, <CONTEXT_JSON>, or <CONTEXT_TOON> tags. **NEVER** use external knowledge, speculate, or invent facts.
    * **Internal Verification:** Before writing, verify that the synthesized answer is fully supported by the <CONTEXT>. Do not show this verification step.
    * **Source Text Adherence:** Construct your answer using the information from the source text. Be concise and directly address the question — do not copy large blocks of text verbatim.
    * **COMPLETENESS:** Ensure your answer fully addresses all parts of the question using all relevant information available in the context. Provide detailed, comprehensive answers that cover all relevant facts, figures, and explanations found in the context.

2.  **UNANSWERABLE CONDITION:**
    * If the provided context tags do not contain sufficient information to fully answer the user's <QUERY>, respond with: `No answer found in the provided context.`

3. **ANSWER-ONLY OUTPUT CONSTRAINT:**
   * Output **ONLY** the final answer that directly responds to the user's <QUERY>.
   * Do **NOT** include any additional commentary beyond what is strictly required to answer the question.

4. **CITATION FORMAT (MANDATORY IF ANSWERABLE):**
   * Track which source file(s) you actually used to construct the answer.
   * Include **ONLY** the source filenames that directly support the statements in your answer.
   * Do **NOT** include sources that were provided but not used.
   * Append citations at the very end of the answer with:
     - a newline before the citation line, and
     - the entire citation in *italics* using this exact format:
       `\\n*(Sources: file_a.pdf, file_b.pdf)*`
   * If only one source is used, list only that one filename.

5.  **FORMAT:**
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


def build_user_message_toon_context(rag_context_toon: str, user_query: str) -> str:
    """
    Build the user message using TOON-encoded context documents and query.

    Args:
        rag_context_toon: TOON-encoded normalized parent document payload
        user_query: The user's question

    Returns:
        Formatted user message string with TOON context
    """
    return f"""<CONTEXT_TOON>
{rag_context_toon}
</CONTEXT_TOON>

<QUERY>
{user_query}
</QUERY>

<FINAL_ANSWER>"""
