from beam import endpoint, Image
from transformers import AutoTokenizer, AutoModelForCausalLM
import torch
import os

# ============================================================
# Model Setup
# ============================================================
MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"


def load_model():
    print(f"🚀 Loading model: {MODEL_ID}")

    # Get HF token from Beam secrets
    hf_token = os.getenv("HUGGINGFACE_HUB_TOKEN")
    if not hf_token:
        raise ValueError("❌ Missing Hugging Face token! Set HUGGINGFACE_HUB_TOKEN in Beam secrets.")
    else:
        print("✅ Hugging Face token found. Authenticating...")

    # Load tokenizer and model with HF token
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, token=hf_token)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        token=hf_token,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        device_map="auto"
    )

    print("✅ Model loaded successfully!")
    return {"tokenizer": tokenizer, "model": model}


# ============================================================
# Beam Endpoint
# ============================================================
@endpoint(
    name="qwen-1_5b-answer-generator",
    on_start=load_model,
    secrets=["HUGGINGFACE_HUB_TOKEN"],
    gpu="RTX4090",
    image=Image().add_python_packages([
        "torch",
        "transformers",
        "accelerate",
        "pydantic"
    ])
)
def generate_answer(context, rag_context: str, user_query: str, max_new_tokens: int = 400):
    """
    RAG Answer Generator Endpoint.

    Args:
        rag_context (str): Retrieved document chunks.
        user_query (str): The user's question.
        max_new_tokens (int): Max answer length.

    Returns:
        dict: Clean final answer grounded only in the context.
    """

    tokenizer = context.on_start_value["tokenizer"]
    model = context.on_start_value["model"]

    # ============================================================
    # Build Answer-Generation Prompt
    # ============================================================
    prompt = f"""
You are an Answer Generation Assistant for a Retrieval-Augmented Generation (RAG) system.

You will receive:
1. A set of context documents retrieved from a vector database.
2. A user query.

Your tasks:
- Read ONLY the information in the context.
- Produce a clear, structured answer (use headings, bullet points, and short paragraphs).
- If the context does NOT contain enough information to answer the question,
  respond with exactly:
  "No answer found in the provided context."

Rules:
- Do NOT use outside knowledge.
- Do NOT guess.
- Do NOT add information not supported by the context.
- Your answer must be fully grounded in the given context.

--------------------
CONTEXT:
{rag_context}
--------------------

USER QUERY:
{user_query}

FINAL ANSWER:
"""

    # ============================================================
    # Apply Qwen Chat Template (Required for Correct Inference)
    # ============================================================
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": prompt}
    ]

    inputs = tokenizer.apply_chat_template(
        messages,
        return_tensors="pt"
    ).to(model.device)

    # ============================================================
    # Generate Answer
    # ============================================================
    outputs = model.generate(
        input_ids=inputs,
        max_new_tokens=max_new_tokens,
        do_sample=False,
        temperature=0.0
    )

    decoded = tokenizer.decode(outputs[0], skip_special_tokens=True)

    # ============================================================
    # Extract ONLY the answer (after FINAL ANSWER:)
    # ============================================================
    marker = "FINAL ANSWER:"
    if marker in decoded:
        answer_only = decoded.split(marker)[-1].strip()
    else:
        answer_only = decoded.strip()

    return {
        "user_query": user_query,
        "answer": answer_only
    }
