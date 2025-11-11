# Embedding Model Service (FastAPI + Docker + Beam)

This service hosts the **text embedding model** (`all-MiniLM-L6-v2`) on **Beam Cloud**, wrapped in a **FastAPI** API for embedding generation.  
It is fully containerized using **Docker** and can be deployed directly to Beam for scalable use.  

## Project Structure
Models_embedding/
├── app.py # FastAPI app serving the embedding model
├── Dockerfile # Docker build instructions
├── requirements.txt # Python dependencies
├── .beamignore # Ignore unnecessary files during build
├── beam.yaml # Beam deployment configuration
└── README_Embedding_Model.md # This file

 1. Environment Setup

You’ll need **Python 3.9+** and **Docker** installed locally.  
If you’re using Beam, make sure the Beam CLI is installed too.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

2. Running Locally with Docker

docker build -t embedding-service .
docker run -p 8000:8000 embedding-service
Then visit:
http://localhost:8000

3. Deployment on Beam

beam deploy --name embedding-service --entrypoint "uvicorn app:app --host 0.0.0.0 --port 8000"
After deployment, you’ll get a public URL like:
https://<your-app-id>-8000.app.beam.cloud/embed

4. API Usage

**Base URL**  
https://2ffd34bc-8e05-4230-8645-d454759adc66-8000.app.beam.cloud

Endpoint
POST /embed
Request body
{
  "text": "Artificial intelligence is transforming industries."
}
curl example
curl -X POST "https://<your-app-id>-8000.app.beam.cloud/embed" \
     -H "Content-Type: application/json" \
     -d '{"text": "AI is changing the world."}'
Python example
import requests

url = "https://<your-app-id>-8000.app.beam.cloud/embed"
data = {"text": "AI is changing the world."}

response = requests.post(url, json=data)
print(response.json())
Expected response
{
  "embedding": [0.0123, -0.0931, 0.4412, ...]
}

5. Model Info

Property	Value
Current model	sentence-transformers/all-MiniLM-L6-v2
Planned model	embeddingGemma
Framework	FastAPI + sentence-transformers
Hosting	Beam Cloud (Docker)

Notes
Endpoint is public.
Keep this file updated if the model changes to embeddingGemma.

(when you paste, it’s okay if the emojis don’t show perfectly — GitLab markdown will be fine.)

---

## 5. Save and exit nano
- Press **Ctrl + O** → Enter (to save)
- Press **Ctrl + X** (to exit)

Now you have the file in your folder.

---

## 6. Add it to Git
```bash
git add README_Embedding_Model.md

7. Commit it

git commit -m "Add embedding model README similar to LLM doc"

8. Push to your branch

git push origin jitesh-embedding
