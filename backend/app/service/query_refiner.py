import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()

LLM_URL = os.getenv("BEAM_LLM_URL")
LLM_KEY = os.getenv("BEAM_LLM_KEY")

HEADERS = {
    "Authorization": f"Bearer {LLM_KEY}",
    "Content-Type": "application/json"
}

async def refine_query(query: str) -> str:
    """
    Refine user query using Beam LLM (Qwen).
    """
    payload = {
        "prompt": f"Rewrite the following into a clean search query:\n{query}\nRefined:"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(LLM_URL, json=payload, headers=HEADERS) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ValueError(f"LLM request failed ({resp.status}): {text}")

            data = await resp.json()
            return data.get("response", "").strip()
