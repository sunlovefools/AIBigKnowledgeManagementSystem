import requests
import os

# -------------------------------
# ENVIRONMENT CONFIGURATION
# -------------------------------

# URLs of your hosted services (on Beam or university GPU server)
LLM_URL = os.getenv("LLM_URL", "https://api.beam.cloud/v1/qwen-1_8b-inference")
EMBED_URL = os.getenv("EMBED_URL", "https://api.beam.cloud/v1/embedding-service")

# Optional timeout for HTTP requests
TIMEOUT = int(os.getenv("BEAM_TIMEOUT", "60"))

# -------------------------------
# LLM INTERACTION (Qwen)
# -------------------------------

def query_llm(prompt: str, max_new_tokens: int = 256, temperature: float = 0.7, top_p: float = 0.9) -> str:
    """
    Sends a prompt to the hosted Qwen model and returns its generated response.
    """
    payload = {
        "prompt": prompt,
        "max_new_tokens": max_new_tokens,
        "temperature": temperature,
        "top_p": top_p
    }
    try:
        resp = requests.post(f"{LLM_URL}/generate", json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if "response" in data:
            return data["response"]
        elif "error" in data:
            return f"[LLM Error] {data['error']}"
        else:
            return "[LLM Error] Unexpected response format."
    except requests.exceptions.RequestException as e:
        return f"[LLM Connection Error] {str(e)}"


# -------------------------------
# EMBEDDING MODEL INTERACTION
# -------------------------------

def get_embedding(text: str):
    """
    Sends text to the embedding model service and retrieves its embedding vector.
    Returns a list[float].
    """
    try:
        resp = requests.post(f"{EMBED_URL}/embed", json={"text": text}, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("embedding", [])
    except requests.exceptions.RequestException as e:
        print(f"[Embedding Error] {str(e)}")
        return []


# -------------------------------
# HEALTH CHECK HELPERS
# -------------------------------

def check_service_health():
    """
    Optional: Check health of both services to ensure they’re reachable.
    """
    results = {}
    for name, url in {"LLM": LLM_URL, "EMBED": EMBED_URL}.items():
        try:
            r = requests.get(url, timeout=5)
            results[name] = "ok" if r.status_code == 200 else f"fail ({r.status_code})"
        except Exception as e:
            results[name] = f"unreachable ({e})"
    return results
