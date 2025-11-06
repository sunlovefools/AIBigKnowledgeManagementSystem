# Models_embedding/app.py
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
import torch

app = FastAPI(title="Embedding Service")

# -------------------- Model Setup --------------------
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
print(f"Loading embedding model: {MODEL_ID}")

device = "cuda" if torch.cuda.is_available() else "cpu"
model = SentenceTransformer(MODEL_ID, device=device)

# -------------------- Request Schema --------------------
class EmbedRequest(BaseModel):
    text: str

# -------------------- Routes --------------------
@app.get("/")
def health():
    return {"status": "ok", "model": MODEL_ID}

@app.post("/embed")
def embed_text(req: EmbedRequest):
    try:
        embedding = model.encode(req.text).tolist()
        return {"embedding": embedding}
    except Exception as e:
        return {"error": str(e)}
