# Answer Generator LLM (Beam Endpoint)

This directory packages the Beam endpoint that turns retrieved RAG context into a final user-facing answer. It loads **Qwen/Qwen2.5-1.5B-Instruct** and enforces a strict prompt so responses stay grounded in the provided context only.

## Contents

- `app.py` – Defines `load_model()` and the `generate_answer` endpoint. Hugging Face credentials are injected via Beam secrets. The service returns structured answers or the fallback string `No answer found in the provided context.` when the evidence is missing.

## Requirements

1. Python 3.10+ and Beam CLI (`pip install --upgrade beam`).
2. Hugging Face access token with read permissions for `Qwen/Qwen2.5-1.5B-Instruct`.
3. Beam project with GPU quota (default `RTX4090`; switch to `A10G` etc. if budget constrained).

## Deployment Workflow

```bash
cd Models/Model_AnswerGenerator_LLM

# Authenticate Beam CLI
beam login

# Store HF token (only needs to be done once per project)
beam secret create HUGGINGFACE_HUB_TOKEN <hf_token>

# Build and deploy the endpoint
beam deploy app.py
```

The decorator requests the necessary Python packages (`torch`, `transformers`, `accelerate`, `pydantic`). Once deployment finishes, Beam exposes `https://api.beam.cloud/v1/qwen-1_5b-answer-generator`.

## Endpoint Contract

`generate_answer(context, rag_context: str, user_query: str, max_new_tokens: int = 400)`

- **Input JSON**
  ```json
  {
    "rag_context": "…concatenated top-k chunk text…",
    "user_query": "How do I rotate Astra DB keys?",
    "max_new_tokens": 350
  }
  ```
- **Processing**
  1. Inserts the context + query into a structured instruction prompt.
  2. Applies the Qwen chat template for proper inference.
  3. Runs greedy decoding (`do_sample=False`, `temperature=0`).
  4. Extracts only the portion after `FINAL ANSWER:` to remove prompt preamble.
- **Response JSON**
  ```json
  {
    "user_query": "How do I rotate Astra DB keys?",
    "answer": "1. Go to ...\n2. Use the rotate credentials panel..."
  }
  ```
  If the context lacks the answer, the model returns `No answer found in the provided context.` exactly (enforced by the prompt).

## Integrating with Backend

1. Set these variables in `backend/.env` (and CI/env secrets):
   ```
   BEAM_ANSWER_GENERATOR_LLM_URL=https://api.beam.cloud/v1/qwen-1_5b-answer-generator
   BEAM_ANSWER_GENERATOR_LLM_KEY=<beam-api-key>
   ```
2. Backend module `app/service/answer_generator.py` posts `{"rag_context": <joined chunks>, "user_query": <original question>}` and returns the `answer` field to `/query` clients.
3. Ensure the chunk texts fed into `rag_context` are concise and relevant; the model obeys the “context only” rule so noisy inputs degrade output quality.

## Customisation Tips

- **Model swap** – Change `MODEL_ID` and redeploy. Confirm the new checkpoint fits on the selected GPU.
- **Answer format** – Edit the prompt instructions to alter style (JSON, bullet-heavy, etc.).
- **Sampling** – Adjust `max_new_tokens`, `temperature`, `top_p` in `model.generate()` if more creative answers are required. Keep `do_sample=False` for deterministic outputs.
- **Latency/Cost** – Switch to a smaller GPU or quantized model if throughput requirements change.

Redeploy via `beam deploy app.py` after any code or configuration changes so Beam rebuilds the container image.
