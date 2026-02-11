import aiohttp
import asyncio
import os
from typing import List, Dict, Any

# --- Configuration ---
ANSWER_GENERATOR_LLM_PROVIDER = os.getenv("ANSWER_GENERATOR_LLM_PROVIDER", "BEAM")
LLM_API_URL = os.getenv("LOCAL_ANSWER_GENERATOR_LLM_URL")
LLM_API_KEY = os.getenv("LOCAL_ANSWER_GENERATOR_LLM_KEY")

if ANSWER_GENERATOR_LLM_PROVIDER == "BEAM":
    LLM_API_URL = os.getenv("BEAM_ANSWER_GENERATOR_LLM_URL")
    LLM_API_KEY = os.getenv("BEAM_ANSWER_GENERATOR_LLM_KEY")
    print("🪛 Using BEAM Answer Generator LLM configuration.")


HEADERS = {
    "Authorization": f"Bearer {LLM_API_KEY}",
    "Content-Type": "application/json"
}


# --- Service Function ---
async def generate_answer(rag_contents: List[Any], user_query: str) -> str:
    """
    Calls the Answer Generator Endpoint.

    Supports:
    - List[str] (old behavior)
    - List[Dict] (new structured retrieval output)

    Args:
        rag_contents: Retrieved RAG results
        user_query: the user question

    Returns:
        The final structured answer from the Answer Generator LLM.
    """

    if not LLM_API_URL or not LLM_API_KEY:
        raise RuntimeError("Answer Generator config missing. Set LLM_API_URL and LLM_API_KEY.")

    # ---------------------------------------------------------
    # NEW: Handle structured contexts safely
    # ---------------------------------------------------------
    if rag_contents and isinstance(rag_contents[0], dict):
        # Convert structured dicts into formatted context string
        context_blocks = []

        for item in rag_contents:
            filename = item.get("filename", "unknown.pdf")
            page = item.get("page", "N/A")
            content = item.get("chunk_context", "")

            block = f"""
Filename: {filename}
Page: {page}

Content:
{content}
"""
            context_blocks.append(block)

        rag_context = "\n\n".join(context_blocks)

    else:
        # Old behaviour (list of strings)
        rag_context = "\n\n".join(rag_contents)

    # ---------------------------------------------------------

    payload = {
        "rag_context": rag_context,
        "user_query": user_query,
    }

    print("🚀 Sending payload to Answer Generator LLM:")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(LLM_API_URL, json=payload, headers=HEADERS, timeout=500) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(f"Answer Generator API Error ({resp.status}): {error_text}")

                data = await resp.json()
                return data.get("answer", "No answer returned by Answer Generator")

        except asyncio.TimeoutError:
            raise RuntimeError("Answer Generator timed out.")

        except Exception as e:
            raise RuntimeError(f"Answer Generator failed: {str(e)}")
