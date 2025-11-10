#  Qwen 1.8B Inference Service (FastAPI + Docker + Beam)

This service hosts the **Qwen 2.5–1.8B Instruct model** on **Beam Cloud**, wrapped in a **FastAPI** API for text generation.  
It is fully containerized using Docker and can be deployed directly to Beam for scalable inference.

---

##  Project Structure

```
Models_LLM/
├── app.py              # FastAPI app serving Qwen
├── Dockerfile          # Docker build instructions
├── requirements.txt    # Python dependencies
├── .dockerignore       # Ignore unnecessary files during build
└── README.md           # This file
```

---

##  1. Environment Setup

You’ll need **Python 3.11+** and **Docker** installed locally.

```bash
# (Optional) Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\activate    # Windows PowerShell
# OR
source .venv/bin/activate   # macOS/Linux

# Install dependencies for local testing
pip install -r requirements.txt
```

---

##  2. Running Locally (Optional)

You can run the FastAPI app locally before deploying:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Then visit:
```
http://localhost:8000
```

Or test with `curl`:
```bash
curl -X POST http://localhost:8000/generate      -H "Content-Type: application/json"      -d '{"prompt": "Explain the theory of relativity."}'
```

---

##  3. Docker Setup

### Build Docker Image
```bash
docker build -t qwen-llm .
```

### Run the Container Locally
```bash
docker run -p 8000:8000 qwen-llm
```

This will start the model API at:
```
http://localhost:8000
```

---

## ☁️ 4. Deploy to Beam Cloud

### Login to Beam
Before deploying, ensure you’ve configured your Beam API key:

```bash
beam configure default --token <YOUR_BEAM_TOKEN>
```

### Deploy to Beam
Deploy your Dockerized FastAPI service:

```bash
beam deploy --name qwen-1_8b-inference   --dockerfile Dockerfile   --entrypoint "uvicorn app:app --host 0.0.0.0 --port 8000"   --ports 8000
```

### (Optional) Deploy with GPU
If your plan includes GPU access:

```bash
beam deploy --name qwen-1_8b-inference   --dockerfile Dockerfile   --entrypoint "uvicorn app:app --host 0.0.0.0 --port 8000"   --ports 8000   --gpu nvidia-tesla-t4
```

---

##  5. API Endpoints

###  Health Check
Check if the service is running:
```bash
curl https://api.beam.cloud/v1/qwen-1_8b-inference/
```
**Response:**
```json
{"status": "ok", "model": "Qwen/Qwen2.5-1.8B-Instruct"}
```

---

###  Generate Text
Send a prompt for text generation:

```bash
curl -X POST https://api.beam.cloud/v1/qwen-1_8b-inference/generate      -H "Content-Type: application/json"      -d '{
           "prompt": "What is quantum computing?",
           "max_new_tokens": 128,
           "temperature": 0.7,
           "top_p": 0.9
         }'
```

**Example Response:**
```json
{
  "response": "Quantum computing uses qubits to process information in superposition, allowing certain problems to be solved faster than with classical computers."
}
```

---

##  6. Troubleshooting

| Problem | Solution |
|----------|-----------|
| `No handler or entrypoint specified` | Add `--dockerfile Dockerfile` and `--entrypoint` flags when deploying |
| Beam cannot find config | Run `beam configure default --token <YOUR_TOKEN>` |
| Deployment failed | Check Dockerfile name and location (case-sensitive) |
| Model loads slowly | Use a GPU (`--gpu nvidia-tesla-t4`) to accelerate model loading |

---

##  7. Project Notes

- The service uses **Qwen 2.5–1.8B Instruct** from Hugging Face (`Qwen/Qwen2.5-1.8B-Instruct`).
- Model loading automatically detects GPU if available.
- API follows standard JSON I/O schema, easy to integrate into any backend.
- Uses **FastAPI** for speed and Beam’s autoscaling for cost efficiency.

---

##  Example Backend Integration

If you want to call this model from your main backend FastAPI service:

```python
import requests

QWEN_URL = "https://api.beam.cloud/v1/qwen-1_8b-inference/generate"

def generate_text(prompt: str):
    response = requests.post(QWEN_URL, json={
        "prompt": prompt,
        "max_new_tokens": 200,
        "temperature": 0.7
    })
    return response.json().get("response", "")
```

---

##  Summary

| Feature | Description |
|----------|--------------|
| **Model** | Qwen 2.5–1.8B Instruct |
| **Framework** | FastAPI |
| **Deployment** | Docker + Beam Cloud |
| **API Endpoints** | `/` (health), `/generate` (text generation) |
| **Default Port** | 8000 |
| **Scalability** | Beam handles scaling automatically |

---

**Author:** Ronith
**Model:** Qwen/Qwen2.5-1.8B-Instruct  
**Tech Stack:** Python 3.11, FastAPI, Transformers, Docker, Beam Cloud  
**Deployment Date:** 10/11/2025
