import aiohttp
import asyncio
import os

# ============================================================
# Beam Answer Generator Configuration
# ============================================================
BEAM_ANSWER_URL = os.getenv("BEAM_ANSWER_GENERATOR_LLM_URL")  # e.g. https://api.beam.cloud/v1/qwen-1_5b-answer-generator
BEAM_ANSWER_KEY = os.getenv("BEAM_ANSWER_GENERATOR_LLM_KEY")  # Your Beam API Key


HEADERS = {
    "Authorization": f"Bearer {BEAM_ANSWER_KEY}",
    "Content-Type": "application/json"
}


# ============================================================
# Call Beam Answer Generator (Async)
# ============================================================
async def generate_answer(rag_contents: list[str], user_query: str) -> str:
    """
    Calls the Beam Answer Generator Endpoint with:
    - rag_context (string)
    - user_query (string)

    Args:
        rag_contents: list of text chunks returned by similarity search
        user_query: the user question

    Returns:
        The final structured answer from Beam LLM.
    """

    if not BEAM_ANSWER_URL or not BEAM_ANSWER_KEY:
        raise RuntimeError("Beam Answer Generator config missing. Set BEAM_ANSWER_URL and BEAM_ANSWER_KEY.")

    # Convert list of chunks into a single context string
    rag_context = "\n\n".join(rag_contents)

    payload = {
        "rag_context": rag_context,
        "user_query": user_query,
        # "max_new_tokens": 350 (optional, it is defaulted 400 at Beam endpoint)
    }

    # Debug: Print payload
    print("🚀 Sending payload to Beam Answer Generator:")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(BEAM_ANSWER_URL, json=payload, headers=HEADERS, timeout=60) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(f"Beam Answer API Error ({resp.status}): {error_text}")

                data = await resp.json()
                return data.get("answer", "No answer returned by Beam")
        
        except asyncio.TimeoutError:
            raise RuntimeError("Beam Answer Generator timed out.")
        
        except Exception as e:
            raise RuntimeError(f"Beam Answer Generator failed: {str(e)}")

