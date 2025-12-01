from beam import endpoint, Image
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
import torch
import os

# -----------------------------
# Model setup
# -----------------------------
MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"

def load_model():
    print(f"🚀 Loading model: {MODEL_ID}")

    # Get the Hugging Face token from Beam secrets
    hf_token = os.getenv("HUGGINGFACE_HUB_TOKEN")
    if not hf_token:
        raise ValueError("❌ Missing Hugging Face token! Please set HUGGINGFACE_HUB_TOKEN in Beam secrets.")
    else:
        print("✅ Hugging Face token found. Authenticating...")

    # Load model and tokenizer with authentication
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, token=hf_token)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        token=hf_token,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        device_map="auto"
    )

    generator = pipeline(
        "text-generation",
        model=model,
        tokenizer=tokenizer,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        device_map="auto"
    )

    print("✅ Model loaded successfully!")
    return generator


# -----------------------------
# Beam Endpoint Definition
# -----------------------------
@endpoint(
    name="qwen-1_5b-query-refiner",
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
def refine_query(context, user_query: str, max_new_tokens: int = 100):
    """
    Query Refiner Endpoint for RAG.

    Args:
        user_query: The raw question from the user.
        max_new_tokens: Output length (short, controlled).

    Returns:
        refined_query: A short, high-quality, embedding-optimized query.
    """

    # Build refined prompt
    refinement_prompt = f"""
You are a Query Refiner Assistant for a Retrieval-Augmented Generation (RAG) system.

Your task:
- Rewrite the user's query into a clearer, more explicit version optimized for embedding-based similarity search.
- It MUST be short, precise, and focused on the user's intent.
- The refined query must be ONE sentence under 30 words.
- Do NOT include explanations, politeness, or extra text.
- Output ONLY the refined query.

User query: {user_query}

Refined query:
    """

    generator = context.on_start_value

    # Deterministic generation
    outputs = generator(
        refinement_prompt,
        max_new_tokens=max_new_tokens,
        do_sample=True,
        top_p=0.9,
        temperature=0.2,
        top_k=20,
        pad_token_id=generator.tokenizer.eos_token_id
    )

    full_output = outputs[0]["generated_text"]

    # Extract only after "Refined query:"
    refined = full_output.split("Refined query:")[-1].strip()

    return {
        "original_query": user_query,
        "refined_query": refined
    }
