# Query Refiner LLM (Beam Endpoint)

This folder contains the Beam deployment for the **Query Refiner** used in the Retrieval-Augmented Generation (RAG) pipeline.  
It hosts `Qwen/Qwen2.5-1.5B-Instruct` and exposes a lightweight `/refine_query` endpoint that takes a raw user query and rewrites it into a short, embedding-friendly sentence before the backend performs similarity search.

## Components

- `app.py` – Beam entrypoint. Defines:
  - `MODEL_ID`: upstream Hugging Face checkpoint.
  - `load_model()`: downloads tokenizer/model with the injected Hugging Face token and preloads a `transformers` text-generation pipeline on the GPU.
  - `refine_query(...)`: endpoint decorated with `@endpoint(...)` that enforces a strict prompt instructing the model to output a concise query.
- (Optional) Add `beam.yaml`, `.beamignore`, or `requirements.txt` alongside if the service grows; currently dependencies are provided inline through `Image().add_python_packages(...)`.

## Requirements

1. Python 3.10+ and the Beam CLI (`pip install --upgrade beam`).
2. Beam account with GPU quota (service requests an `RTX4090`; adjust to `A10G` or others if required).
3. Hugging Face access token (`HUGGINGFACE_HUB_TOKEN`) that can read `Qwen/Qwen2.5-1.5B-Instruct`.

## Deployment Workflow

```bash
cd Models/Model_Query_LLM

# 1. Authenticate
beam login

# 2. Store the Hugging Face token so load_model() can read it
beam secret create HUGGINGFACE_HUB_TOKEN <hf_token_value>

# 3. Deploy the endpoint defined in app.py
beam deploy app.py
```

- `beam deploy` builds an image that installs `torch`, `transformers`, `accelerate`, and `pydantic`, provisions an `RTX4090`, and runs `load_model()` once per replica so the weights stay in GPU memory.
- After deployment Beam exposes the HTTPS endpoint at `https://api.beam.cloud/v1/qwen-1_5b-query-refiner`.

## Endpoint Contract

`refine_query(context, user_query: str, max_new_tokens: int = 100)`:

- **Input JSON**
  ```json
  {
    "user_query": "How do I rotate database credentials in Astra?",
    "max_new_tokens": 80
  }
  ```
- **Behavior**
  - Builds a prompt instructing the model to produce one sentence (<30 words) optimized for vector search.
  - Uses deterministic-ish sampling (`temperature=0.2`, `top_p=0.9`, `top_k=20`) to keep outputs focused.
  - Extracts only the text after `Refined query:` to avoid prompt leakage.
- **Output JSON**
  ```json
  {
    "original_query": "How do I rotate database credentials in Astra?",
    "refined_query": "Procedure for rotating Astra DB credentials and keys."
  }
  ```

## Integrating with the Backend

1. Store the Beam endpoint URL (`https://api.beam.cloud/v1/qwen-1_5b-query-refiner`) and optional auth key in `backend/.env`:
   ```
   QUERY_REFINER_URL=https://api.beam.cloud/v1/qwen-1_5b-query-refiner
   ```
2. Call `POST {QUERY_REFINER_URL}/refine_query` from the ingestion or query service before generating embeddings.
3. Pass the returned `refined_query` to the embedding service (`Models/Models_embedding`) so searches stay consistent.

## Customisation Tips

- **Different Model**: change `MODEL_ID` and ensure the new checkpoint fits on the selected GPU.
- **Generation Style**: tweak `max_new_tokens`, `temperature`, or the system prompt string to encourage longer/shorter rewrites.
- **Throughput**: reduce `max_new_tokens` or switch to a smaller GPU if latency/cost trade-offs require it.

Redeploy (`beam deploy app.py`) after any change so Beam rebuilds the container with the updated configuration.

