# Models Folder Documentation

This directory contains every LLM microservice powering the Knowledge Management platform. Each model runs as an independent Beam endpoint so we can scale, monitor, and iterate on them without redeploying the rest of the stack. Use the sections below to understand what each service does and follow the links to their dedicated READMEs for deployment and integration details.

---

## 1. Query Refiner – `Model_Query_LLM`

- **Goal**: Convert raw, sometimes noisy user questions into short, explicit sentences optimized for embedding-based similarity search. This boosts vector recall and keeps the RAG pipeline focused.
- **Model**: `Qwen/Qwen2.5-1.5B-Instruct`, hosted on Beam as `qwen-1_5b-query-refiner`.
- **Inputs**: Raw `user_query` string + optional `max_new_tokens` override.
- **Outputs**: JSON with the original query and a `refined_query` trimmed to <30 words.
- **Backend Integration**: `backend/app/service/query_refiner.py` calls the Beam endpoint before embedding the query in `/query`.
- **Documentation**: [Detailed README](./Model_Query_LLM/README.md) – Beam CLI setup, secret management, deployment commands, endpoint contract, and customization ideas (prompt tweaks, GPU swaps, sampling config).

### Quick Reference
| Requirement | Value |
|-------------|-------|
| Env secret  | `HUGGINGFACE_HUB_TOKEN` |
| GPU         | `RTX4090` (adjust if needed) |
| Beam URL    | `https://api.beam.cloud/v1/qwen-1_5b-query-refiner` |
| Backend vars| `BEAM_REFINE_LLM_URL`, `BEAM_REFINE_LLM_KEY` |

---

## 2. Answer Generator – `Model_AnswerGenerator_LLM`

- **Goal**: Produce the final response users see in chat. It takes the top-k retrieved chunks plus the original user query, then returns a structured, context-grounded answer.
- **Model**: `Qwen/Qwen2.5-1.5B-Instruct`, hosted on Beam as `qwen-1_5b-answer-generator`.
- **Inputs**: `rag_context` (concatenated chunk text), `user_query`, optional `max_new_tokens`.
- **Outputs**: JSON with `answer`. If the evidence is missing, it replies with `No answer found in the provided context.` (prompt-enforced).
- **Backend Integration**: `backend/app/service/answer_generator.py` calls this endpoint at the end of `/query`.
- **Documentation**: [Detailed README](./Model_AnswerGenerator_LLM/README.md) – deployment workflow, prompt template, backend `.env` variables, and tuning suggestions.

### Quick Reference
| Requirement | Value |
|-------------|-------|
| Env secret  | `HUGGINGFACE_HUB_TOKEN` |
| GPU         | `RTX4090` (change to `A10G` etc. if needed) |
| Beam URL    | `https://api.beam.cloud/v1/qwen-1_5b-answer-generator` |
| Backend vars| `BEAM_ANSWER_GENERATOR_LLM_URL`, `BEAM_ANSWER_GENERATOR_LLM_KEY` |

---

## Operating the Model Suite

1. **Secrets** – All Beam services rely on the same Hugging Face token. Run `beam secret create HUGGINGFACE_HUB_TOKEN <token>` or configure it in the Beam console before deployment.
2. **Deployments** – After editing any `app.py` (prompt changes, different `MODEL_ID`, GPU type), redeploy with `beam deploy app.py` from the corresponding folder.
3. **Backend Wiring** – Keep backend environment variables in sync with the live Beam URLs + API keys. `query_refiner.py` and `answer_generator.py` log failures if endpoints move.
4. **Versioning** – Treat each model folder as a separately versioned artifact. Record major changes in its README and consider tagging Beam deployments for traceability.
5. **Monitoring** – Use Beam’s dashboard (logs, GPU usage, invocation counts) to debug timeouts or scaling issues without touching backend code.

For full instructions—including example curl requests, schema diagrams, and troubleshooting tips—open the linked READMEs in each model’s directory.
