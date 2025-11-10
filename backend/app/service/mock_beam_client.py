"""
Mock Beam Client
----------------
This file simulates the behavior of `beam_client.py` for local testing.
No external network calls are made.
"""

import random

# -------------------------------
# MOCK LLM INTERFACE
# -------------------------------

def query_llm(prompt: str, max_new_tokens: int = 256, temperature: float = 0.7, top_p: float = 0.9) -> str:
    """
    Simulate an LLM response for testing the orchestration pipeline.
    """
    print("🧠 [MOCK] Simulating LLM response...")
    return f"[MOCK LLM] Response to prompt: '{prompt[:60]}...'\n(Simulated answer for testing purposes)"

# -------------------------------
# MOCK EMBEDDING INTERFACE
# -------------------------------

def get_embedding(text: str):
    """
    Return a mock 768-dimensional embedding vector for testing retrieval.
    """
    print("📊 [MOCK] Generating fake embedding vector...")
    return [random.random() for _ in range(768)]

# -------------------------------
# MOCK HEALTH CHECK
# -------------------------------

def check_service_health():
    """
    Simulate a healthy response for both LLM and embedding services.
    """
    print("✅ [MOCK] Beam service health OK (simulated).")
    return {"LLM": "mocked", "EMBED": "mocked"}
