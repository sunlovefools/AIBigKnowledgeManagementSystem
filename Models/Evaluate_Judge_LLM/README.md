# Local Judge Service (Ragas Compatible)
A specialized, local microservice designed to act as the LLM Judge for evaluating the RAG system.

This service wraps a local Ollama model (Qwen 2.5:14B) in a FastAPI application that mimics the OpenAI API structure (/v1/chat/completions). This allows evaluation frameworks like Ragas to connect to it seamlessly, treating your local machine as if it were GPT-4, but without the API costs.

## 🛠️ Prerequisites

Before running the service, ensure you have the following installed:

1.  **Python 3.10+**
2.  **Ollama**: You must have Ollama installed and running. [Download Ollama here](https://ollama.com/).
3. **uv**: For Python dependency management. [Install uv here](https://docs.astral.sh/uv/getting-started/installation/).

### Model Setup
This service is hardcoded to use the `qwen2.5:14b` model. You must pull this model before starting the app (More details in the Running the Service section):

Note: If you wish to use a different model, update the MODEL_ID variable in engine.py.

## 🏃‍♂️ Running the Service

This service uses **Ollama** for the local LLM backend and **uv** for Python dependency management (via `uv.lock`).

## 1) Start Ollama server and pull the model

Ollama must be running locally (default: `http://127.0.0.1:11434`) and the model **`qwen2.5:14b`** must be pulled.

```Bash
# Start the Ollama server (keep this running in a terminal)
ollama serve
```
In a separate terminal, pull the required model:

```Bash
ollama pull qwen2.5:14b
```

To verify the model is available:
```Bash
# You should see qwen2.5:14b listed
ollama list
```

## 2) Set up Python environment with uv
Make sure that you are in correct directory where this README.md file is located.

You can check the directory by running:
```Bash
ls # You should see README.md, main.py, uv.lock and other associated files under Evaluate_Judge_LLM directory
```

Then you create and activate a new uv environment:
```Bash
uv venv # Create a new uv environment
```

Activate the environment:
```Bash
.venv/Scripts/activate  # On Windows
source .venv/bin/activate  # On macOS/Linux
```

Synchronize the environment with the dependencies specified in `uv.lock`:
```Bash
uv sync
```
## 3) Configure Secure Access Token
To ensure secure access to the Judge service, set an environment variable for the access token. This token will be required for any client (like Ragas) to interact with the Judge API.

## 4) Start the Answer Service  
Start the Answer Service: Run the application using Python. It will start on port 8002.

```Bash
python main.py
```

Output should indicate: 🚀 Starting Local Answer Service (Ollama) on http://localhost:8001

# 3. Use in evaluation
# results = evaluate(..., metrics=[answer_correctness], llm=ragas_judge)
🌐 Public Access (via ngrok)
If you are running Ragas on a cloud notebook (like Colab) but want to use your local GPU for judging, use ngrok.

Start the Tunnel (Forwarding Port 8002)

Bash
ngrok http 8002
Update Ragas Config Replace http://localhost:8002/v1 in the python snippet above with your new ngrok URL: https://<your-id>.ngrok-free.app/v1

🧪 Testing the API
You can test the judge manually using curl. This verifies that the OpenAI-compatible endpoint is working.

Bash
curl -X POST "http://localhost:8002/v1/chat/completions" \
     -H "Authorization: Bearer your_secure_judge_token_here" \
     -H "Content-Type: application/json" \
     -d '{
           "model": "qwen2.5:14b",
           "messages": [
             {"role": "system", "content": "You are a helpful assistant."},
             {"role": "user", "content": "Rate this answer from 1 to 5: The sky is blue."}
           ]
         }'
Example Response
JSON
{
  "id": "chatcmpl-judge",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "default",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "5"
      },
      "finish_reason": "stop"
    }
  ]
}