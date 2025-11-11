# Qwen LLM Deployment (FastAPI + Beam)

## Overview
This service deploys the Qwen 2.5-0.5B Instruct model using FastAPI and hosts it on Beam for remote inference.  
It provides a RESTful API to generate text responses from the LLM.

---

## Folder Structure
```
Models_LLM/
│
├── app.py              # FastAPI application with model loading and endpoints
├── Dockerfile          # Docker setup for the API
├── beam.yaml           # Beam configuration file (for documentation only)
├── requirements.txt    # Python dependencies
└── llm-test.html       # Simple web interface to test the LLM
```

---

## API Endpoints

### 1. Health Check
**Endpoint:** `GET /health`  
**Description:** Returns a simple health status and model name.  
**Example Response:**
```json
{
  "status": "ok",
  "model": "Qwen/Qwen2.5-0.5B-Instruct"
}
```

### 2. Text Generation
**Endpoint:** `POST /`  
**Description:** Generates text using the Qwen model.  
**Request Body Example:**
```json
{
  "prompt": "Explain black holes simply.",
  "max_new_tokens": 50,
  "temperature": 0.7,
  "top_p": 0.9
}
```
**Response Example:**
```json
{
  "response": "A black hole is a region in space where gravity is so strong that nothing can escape..."
}
```

---

## Deployment (via Beam)

To redeploy this service, run the following command inside the `Models_LLM` directory:

```bash
beam deploy --name qwen-0_5b-inference --dockerfile Dockerfile --entrypoint "uvicorn app:app --host 0.0.0.0 --port 8000" --ports 8000
```

Once deployed, Beam will provide a public endpoint similar to:
```
https://<deployment-id>-8000.app.beam.cloud
```

---

## Using the Web Interface
You can test the model locally or online using the provided `llm-test.html` file.

1. Open `llm-test.html` in a browser.
2. Paste your Beam endpoint (without `/generate`).
3. Type a prompt (e.g., "Explain black holes").
4. Click Generate to see the model’s response.

---

## Requirements
Ensure the following Python packages are installed (they are already listed in `requirements.txt`):
```
fastapi
uvicorn
torch
transformers
pydantic
```

---

## Example Endpoint
If deployed successfully, your API should be accessible at:
```
https://d1fd6aaa-84e1-4e6c-bafd-68052b87fc87-8000.app.beam.cloud
```

To query directly from the command line:
```bash
curl -X POST https://d1fd6aaa-84e1-4e6c-bafd-68052b87fc87-8000.app.beam.cloud   -H "Content-Type: application/json"   -d '{"prompt": "Explain black holes", "max_new_tokens": 50}'
```

---

## Notes
- The model runs on CPU by default. To use GPU, set the accelerator in `beam.yaml` to `"nvidia-tesla-t4"`.
- The `/docs` route is not exposed externally on Beam; use `/` for inference.
- You can redeploy updates by re-running the `beam deploy` command.
