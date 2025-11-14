import os
import aiohttp
import asyncio
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

BEAM_EMBEDDING_URL = os.getenv("BEAM_EMBEDDING_URL")
BEAM_EMBEDDINGS_KEY = os.getenv("BEAM_EMBEDDINGS_KEY")

HEADERS = {
    "Authorization": f"Bearer {BEAM_EMBEDDINGS_KEY}",
    "Content-Type": "application/json",
    "Connection": "keep-alive"
}


# -----------------------------------------------------------
# Send the full payload directly to Beam
# -----------------------------------------------------------
async def embed_text(session: aiohttp.ClientSession, payload: dict):
    """
    Send the entire payload to the Beam embedding endpoint asynchronously.

    Args:
        session (aiohttp.ClientSession): The active HTTP session.
        payload (dict): The JSON body to send (e.g., {"documents": [text1, text2, ...]}).

    Returns:
        dict: The response JSON from the embedding server.
              Expected: {"embeddings": [[float, ...], ...]}
    """
    try:
        async with session.post(
            BEAM_EMBEDDING_URL,
            json=payload,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=60)
        ) as response:
            # If response status is not successful, raise an error and log details
            if response.status != 200:
                detail = await response.text()
                raise Exception(f"Embedding request failed (status {response.status}): {detail}")

            # Parse and return the JSON response
            data = await response.json()
            return data  # should contain "embeddings"
    except Exception as e:
        print(f"❌ Embedding request failed: {e}")
        return {"embeddings": None, "error": str(e)}

