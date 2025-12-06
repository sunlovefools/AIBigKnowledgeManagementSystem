from beam import endpoint, Image
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
import torch
import os

# -----------------------------
# Configuration
# -----------------------------
MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"

# -----------------------------
# Model setup
# -----------------------------
def load_model():
    """
    Loads the tokenizer and model, then initializes the text generation pipeline.
    This function runs once when the Beam endpoint starts up (on_start).
    """
    print(f"🚀 Loading model: {MODEL_ID}")

    # Get the Hugging Face token from Beam secrets
    hf_token = os.getenv("HUGGINGFACE_HUB_TOKEN")
    if not hf_token:
        # Beam will stop the deployment if this essential secret is missing
        raise ValueError("❌ Missing Hugging Face token! Please set HUGGINGFACE_HUB_TOKEN in Beam secrets.")
    else:
        print("✅ Hugging Face token found. Authenticating...")

    # Load model and tokenizer with authentication
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, token=hf_token)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        token=hf_token,
        # Use float16 for GPU performance
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        # 'auto' or "cuda" can be used to leverage the specified GPU
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
    # Ensure you specify a powerful GPU for this model
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
    Query Refiner Endpoint for RAG using Qwen 1.5B with deterministic, 
    search-optimized prompting.
    """

    # 1. Define the System Prompt/Instruction (STRICT OUTPUT)
    system_prompt = (
        "You are a Query Refiner Assistant for a Retrieval-Augmented Generation (RAG) system. "
        "Your task: Rewrite the user's query into a clearer, more explicit version optimized for embedding-based similarity search. "
        "It MUST be short, precise, and focused on the user's intent. "
        "The refined query must be ONE sentence under 30 words. "
        "Do NOT include explanations, politeness, or extra text. Output ONLY the refined query."
    )
    
    # 2. Construct the full ChatML-style prompt
    refinement_prompt = (
        f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
        f"<|im_start|>user\nUser query: {user_query}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )

    # The generator is retrieved from the context, which holds the return value of on_start
    generator = context.on_start_value 

    # Deterministic generation (Greedy decoding)
    outputs = generator(
        refinement_prompt,
        max_new_tokens=max_new_tokens,
        do_sample=False,        # <-- Ensures deterministic output
        pad_token_id=generator.tokenizer.eos_token_id,
        return_full_text=False # Crucial for getting only the new generated text
    )

    full_output = outputs[0]["generated_text"]
    
    # ----------------------------------------
    # Aggressive Cleanup Logic
    # ----------------------------------------
    refined = full_output.strip()

    # Step 1: Trim the output to the first line
    refined = refined.split('\n')[0].strip()
    
    # Step 2: Aggressively remove everything after the first period ('.') 
    if '.' in refined:
        refined = refined.split('.')[0] + '.'
    
    # Step 3: Final trim
    refined = refined.strip()
    
    # ----------------------------------------

    return {
        "original_query": user_query,
        "refined_query": refined
    }