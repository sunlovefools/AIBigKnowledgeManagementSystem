# Models_embedding/app.py
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoTokenizer, AutoModel
import torch
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

app = FastAPI(title="Gemma Embedding Service")

# -------------------- Config --------------------
MODEL_ID = os.getenv("MODEL_ID", "google/gemma-2b")
device = "cuda" if torch.cuda.is_available() else "cpu"

print(f"Loading embedding model: {MODEL_ID} on {device}")

# Load model and tokenizer
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
model = AutoModel.from_pretrained(MODEL_ID).to(device)

# -------------------- Request schema --------------------
class EmbedRequest(BaseModel):
    text: str

# -------------------- Routes --------------------
@app.get("/")
def health():
    return {"status": "ok", "model": MODEL_ID}

@app.post("/embed")
def embed_text(req: EmbedRequest):
    try:
        inputs = tokenizer(req.text, return_tensors="pt", truncation=True, padding=True).to(device)
        with torch.no_grad():
            outputs = model(**inputs)
            # Average last hidden state to create a single vector
            embedding = outputs.last_hidden_state.mean(dim=1).squeeze().cpu().tolist()
        return {"embedding": embedding}
    except Exception as e:
        return {"error": str(e)}
