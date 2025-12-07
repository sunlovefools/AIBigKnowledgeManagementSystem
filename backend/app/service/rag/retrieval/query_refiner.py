import os
import aiohttp

LLM_URL = os.getenv("BEAM_REFINE_LLM_URL")
LLM_KEY = os.getenv("BEAM_REFINE_LLM_KEY")

HEADERS = {
    "Authorization": f"Bearer {LLM_KEY}",
    "Content-Type": "application/json"
}

async def refine_query(query: str) -> str:
    """
    Sends user_query to Beam LLM (Qwen Query Refiner).
    """

    payload = {
        "user_query": query
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(LLM_URL, json=payload, headers=HEADERS) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ValueError(f"LLM request failed ({resp.status}): {text}")

            data = await resp.json()

            # Beam returns {"original_query": "...", "refined_query": "..."}
            return data.get("refined_query", "").strip()