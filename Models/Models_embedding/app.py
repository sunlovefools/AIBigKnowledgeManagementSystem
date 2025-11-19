from pydantic import BaseModel
import torch
from beam import endpoint, Image
from sentence_transformers import SentenceTransformer
import os


# -----------------------------
# Define request structure
# -----------------------------
class EmbedRequest(BaseModel):
    input: list[str]


# -----------------------------
# Model loading
# -----------------------------
def load_model():
    print("🚀 Loading EmbeddingGemma model...")

    hf_token = os.getenv("HUGGINGFACE_HUB_TOKEN")
    if not hf_token:
        print("❌ No Hugging Face token found in environment!")
    else:
        print("✅ Hugging Face token found, authenticating...")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(
        "google/embeddinggemma-300m",
        device=device,
        use_auth_token=hf_token,
    )

    print("✅ Model loaded successfully!")
    return model


# -----------------------------
# Beam endpoint
# -----------------------------
@endpoint(
    name="embedding-gemma",
    on_start=load_model,
    secrets=["HUGGINGFACE_HUB_TOKEN"],  # ensure token is injected
    workers = 1,
    gpu = "RTX4090",
    image=Image().add_python_packages([
        "torch",
        "sentence-transformers",
        "pydantic",
    ])
)
def embed(context, input: str):
    """Generate embeddings for the input text using EmbeddingGemma model."""
    # Retrieve preloaded model from context
    model = context.on_start_value

    # Encode query and documents
    query_embedding = model.encode(input, convert_to_tensor=True)

    embedding = query_embedding.cpu().tolist()

    # Return the results as a JSON response
    return {"embedding": embedding}