from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
import torch

app = FastAPI(title="Qwen 2.5-0.5B Inference Service")

MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"

print(f"Loading {MODEL_ID} ...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID,
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

class GenRequest(BaseModel):
    prompt: str
    max_new_tokens: int = 256
    temperature: float = 0.7
    top_p: float = 0.9

# Health check on /health
@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID}

# MAIN LLM endpoint on /
@app.post("/")
def generate_text(req: GenRequest):
    try:
        outputs = generator(
            req.prompt,
            max_new_tokens=req.max_new_tokens,
            temperature=req.temperature,
            top_p=req.top_p,
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id
        )
        return {"response": outputs[0]["generated_text"]}
    except Exception as e:
        return {"error": str(e)}
