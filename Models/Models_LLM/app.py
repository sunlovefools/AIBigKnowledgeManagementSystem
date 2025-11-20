from beam import endpoint, Image
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
import torch
import os

# -----------------------------
# Model setup
# -----------------------------
MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"  # ✅ Updated model ID

def load_model():
    print(f"🚀 Loading model: {MODEL_ID}")

    # Load Hugging Face token securely from environment
    hf_token = os.getenv("HUGGINGFACE_HUB_TOKEN")
    if not hf_token:
        raise ValueError("❌ Missing Hugging Face token! Please set HUGGINGFACE_HUB_TOKEN in Beam secrets.")
    else:
        print("✅ Hugging Face token found. Authenticating...")

    # Load tokenizer and model with authentication
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, token=hf_token)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        token=hf_token,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        device_map="auto"
    )

    # Create text generation pipeline
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
    name="qwen-1_5b-inference",  # ✅ Updated endpoint name
    on_start=load_model,
    secrets=["HUGGINGFACE_HUB_TOKEN"],  # Beam securely injects your Hugging Face token
    gpu="A10G",
    image=Image().add_python_packages([
        "torch",
        "transformers",
        "accelerate",
        "pydantic"
    ])
)
def generate_text(context, prompt: str, max_new_tokens: int = 256, temperature: float = 0.7, top_p: float = 0.9):
    """
    Beam endpoint for text generation using Qwen2.5-1.5B-Instruct.

    Args:
        context: Beam runtime context (contains preloaded model).
        prompt: The input text prompt for the model.
        max_new_tokens: Max number of tokens to generate.
        temperature: Sampling temperature for creativity.
        top_p: Nucleus sampling parameter.

    Returns:
        JSON object containing the generated response.
    """
    generator = context.on_start_value
    outputs = generator(
        prompt,
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        top_p=top_p,
        do_sample=True,
        pad_token_id=generator.tokenizer.eos_token_id
    )

    return {"prompt": prompt, "response": outputs[0]["generated_text"]}
