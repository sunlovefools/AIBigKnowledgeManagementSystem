# Answer Generator Service

A lightweight, local microservice designed for **Retrieval-Augmented Generation (RAG)** systems. This service acts as the final "Answer Generation" node: it accepts a user query and retrieved text context, processes them through a local LLM (via Ollama), and returns a strictly grounded response.

## 🚀 Features

* **Strict Grounding:** The system prompt is engineered to answer *only* based on the provided context, reducing hallucinations.
* **Unanswerable Handling:** Automatically returns `No answer found in the provided context.` if the context is insufficient.
* **Local Privacy:** Runs entirely locally using [Ollama](https://ollama.com/) and `qwen2.5:14b`.
* **FastAPI & LangChain:** Built for performance and easy integration using modern Python libraries.

---

## 🛠️ Prerequisites

Before running the service, ensure you have the following installed:

1.  **Python 3.10+**
2.  **Ollama**: You must have Ollama installed and running. [Download Ollama here](https://ollama.com/).

### Model Setup
This service is hardcoded to use the `qwen2.5:14b` model. You must pull this model before starting the app:

```bash
ollama pull qwen2.5:14b
```

Note: If you wish to use a different model, update the MODEL_ID variable in engine.py.

## 📦 Installation
Clone the repository:

```Bash
git clone <your-repo-url>
cd answer-generator
```

Install dependencies: Since a pyproject.toml is provided, you can install the project in editable mode or install the specific packages directly:

```Bash
pip install .
# OR manually
pip install fastapi uvicorn langchain-ollama
```

## 🏃‍♂️ Running the Service

This service uses **Ollama** for the local LLM backend and **uv** for Python dependency management (via `uv.lock`).

### ✅ Requirements
1. **Ollama installed**
   - Download: https://ollama.com/download
2. **uv installed**
   - Install guide: https://docs.astral.sh/uv/getting-started/installation/

---

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
ls # You should see README.md, pyproject.toml,uv.lock and other associated files under Local_Model_AnswerGenerator_LLM directory
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

## 3) Start the Answer Service  
Start the Answer Service: Run the application using Python. It will start on port 8001.

```Bash
python main.py
```

Output should indicate: 🚀 Starting Local Answer Service (Ollama) on http://localhost:8001

## 🌐 Public Access (via ngrok)
You can expose this local service to the public internet using ngrok. This allows external clients to access your API securely via a public URL.

1. Prerequisites
- Ensure ngrok is installed and added to your system PATH.
- Ensure you have authenticated your ngrok account:

```Bash
ngrok config add-authtoken <YOUR_TOKEN>
```

2. Start the Tunnel
Open a new terminal window (leave your Python app running in the first one) and run:

```Bash
ngrok http 8001
```
You will see an output like:

Forwarding    https://<random-id>.ngrok-free.app -> http://localhost:8001
Copy the https URL shown. This is your Public Endpoint.

3. Test the Public Endpoint
You can now send requests to this URL from anywhere.

Example Request (cURL): Replace <your-ngrok-url> and <your-secure-token>.

```Bash

curl -X POST "https://<your-ngrok-url>/generate_answer" \
     -H "Authorization: Bearer <your-secure-token>" \
     -H "Content-Type: application/json" \
     -d '{
           "rag_context": "The Eiffel Tower is located in Paris.",
           "user_query": "Where is the Eiffel Tower?"
         }'
```
## 🔌 API Usage
Endpoint: Generate Answer
POST /generate_answer

Accepts a JSON payload containing the retrieved context chunks and the user's specific query.

Request Schema
```JSON
{
  "rag_context": "string (The text chunks retrieved from your vector DB)",
  "user_query": "string (The question the user asked)"
}
```

Example Response
```JSON
{
  "answer": "Mira dropped the fruit because she tripped on an untied shoelace while refusing to use a bag."
}
```

## 📝 Logging
The service writes detailed logs to both the console and a file named `rag_service.log`.

User Query: The incoming question.
RAG Context: The raw text provided for grounding.
Status: Connection success/failure and inference errors.

To view logs in real-time:

```Bash
tail -f rag_service.log
```