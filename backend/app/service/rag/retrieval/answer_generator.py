import aiohttp
import asyncio
import os

# --- Configuration ---
ANSWER_GENERATOR_LLM_PROVIDER = os.getenv("ANSWER_GENERATOR_LLM_PROVIDER", "BEAM")  # Default to BEAM
LLM_API_URL = os.getenv("LOCAL_ANSWER_GENERATOR_LLM_URL")  # e.g. http://localhost:8001/answer-generator or the ngrok URL
LLM_API_KEY = os.getenv("LOCAL_ANSWER_GENERATOR_LLM_KEY")

if ANSWER_GENERATOR_LLM_PROVIDER == "BEAM":
    LLM_API_URL = os.getenv("BEAM_ANSWER_GENERATOR_LLM_URL")  # e.g. https://api.beam.cloud/v1/qwen-1_5b-answer-generator
    LLM_API_KEY = os.getenv("BEAM_ANSWER_GENERATOR_LLM_KEY")  # Your Beam API Key
    print("🪛 Using BEAM Answer Generator LLM configuration.")


HEADERS = {
    "Authorization": f"Bearer {LLM_API_KEY}",
    "Content-Type": "application/json"
}

# --- Service Function ---
async def generate_answer(rag_contents: list[str], user_query: str) -> str:
    """
    Calls the Answer Generator Endpoint with:
    - rag_context (string)
    - user_query (string)

    Args:
        rag_contents: list of text chunks returned by similarity search
        user_query: the user question

    Returns:
        The final structured answer from the Answer Generator LLM.
    """

    if not LLM_API_URL or not LLM_API_KEY:
        raise RuntimeError("Answer Generator config missing. Set LLM_API_URL and LLM_API_KEY.")

    # Convert list of chunks into a single context string
    rag_context = "\n\n".join(rag_contents)

    payload = {
        "rag_context": rag_context,
        "user_query": user_query,
    }

    # Debug: Print payload
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

