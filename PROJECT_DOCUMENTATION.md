# Project Documentation Map

This guide explains how the repository is organized and links to the subsystem manuals so that any new contributor can ramp up quickly. Work through the sections below in order if you are onboarding for the first time.

---

## 1. Repository Structure & Quick Start

| Path | Purpose |
|------|---------|
| `frontend/` | React + TypeScript SPA that users interact with. |
| `backend/` | FastAPI service orchestrating authentication, ingestion, and query flows. |
| `Models/`  | Beam-hosted LLM microservices (query refiner + answer generator) used by the backend. |

**Immediate next steps for newcomers**
1. Read the root `README.md` for a high-level mission/architecture overview.
2. Follow the subsystem docs linked below to dive into setup, APIs, and deployment specifics.
3. Keep this map handy when switching areas; each section calls out the key commands and `.env` variables you need.

---

## 2. Frontend Guide

- **Full Documentation**: [`frontend/FRONTEND_README.md`](./frontend/FRONTEND_README.md)
- **What it covers**:
  - Stack (React 19, React Router DOM 7, Axios, Vite 7, ESLint/TypeScript configs).
  - `Register` and `MainPage` flows, including all relevant React state variables and Axios calls.
  - Environment management via `VITE_API_BASE` for dev/prod plus commands (`npm run dev`, `npm run lint`, `npm run build`).
  - Integration pointers for future JWT login, error UX, and testing with Vitest/RTL.

**Key takeaways for new devs**
- Install dependencies with `npm install` and run `npm run dev` to reach `http://localhost:5173`.
- Update `.env.development` / `.env.production` if backend URLs change.
- Reference `FRONTEND_README.md` whenever you need component-level behavior, styling notes, or troubleshooting checklists.

---

## 3. Backend Guide

- **Full Documentation**: [`backend/BACKEND_README.md`](./backend/BACKEND_README.md)
- **What it covers**:
  - FastAPI router layout (`/auth`, `/ingest`, `/query`) and how they interact.
  - Data pipelines: ingestion (text extraction → chunking → Beam embeddings → Astra upsert) and query (refinement → embedding → vector search → answer generation).
  - Configuration requirements (`ASTRA_DB_URL/TOKEN`, Beam URLs/keys) plus Docker + local run commands (`uvicorn app.main:app --reload`).
  - Auth service internals (bcrypt hashing, validation helpers), vector store operations, and debugging aids (`vectors_debug.txt`, `polished_chunks_debug.txt`).
  - Deployment + scaling notes (CORS tightening, JWT roadmap, monitoring suggestions).

**Key takeaways for new devs**
- Copy `.env.example` (if provided) or follow the README to set `backend/.env` with Astra/Beam secrets.
- Activate a virtualenv, run `pip install -r requirements.txt`, and start FastAPI with `uvicorn`.
- Keep an eye on the `docs/` folder (`ingestion.md`, `query.md`, `Vector_DB.md`) for deeper dives while working through the main README.

---

## 4. LLM Services (Beam)

- **Index README**: [`Models/README.md`](./Models/README.md)
- **Service READMEs**:
  - Query Refiner – [`Models/Model_Query_LLM/README.md`](./Models/Model_Query_LLM/README.md)
  - Answer Generator – [`Models/Model_AnswerGenerator_LLM/README.md`](./Models/Model_AnswerGenerator_LLM/README.md)

**What you learn there**
- Exact Beam deployment workflow (`beam login`, `beam secret create HUGGINGFACE_HUB_TOKEN`, `beam deploy app.py`).
- Endpoint contracts for both services (input JSON shapes, sample responses, prompt rules).
- Backend integration variables (`BEAM_REFINE_LLM_URL/KEY`, `BEAM_ANSWER_GENERATOR_LLM_URL/KEY`) and how the FastAPI services call them.
- Customization levers (changing `MODEL_ID`, GPU size, prompt templates, sampling parameters) and operational tips (monitoring, versioning).

**Key takeaways for new devs**
- Treat each model directory as its own deployable artifact; update the associated README when making prompt/model changes.
- Ensure Beam secrets exist before deploying; missing `HUGGINGFACE_HUB_TOKEN` will crash the `load_model()` hooks.
- When endpoints or API keys change, update the backend `.env` immediately so query/answer flows keep working.

---

## 5. Putting It All Together

1. **Local loop**:  
   - Start the backend (`uvicorn app.main:app --reload`).  
   - Start the frontend (`npm run dev`).  
   - Point `VITE_API_BASE` to the running backend (typically `http://127.0.0.1:8000`).  
   - Trigger ingestion via the UI to confirm Beam embeddings/Astra storage, then ask a question.
2. **Deployment**:  
   - Keep Beam endpoints up to date (`beam deploy app.py` in each model).  
   - Deploy backend (Docker/AWS EC2 per README).  
   - Deploy frontend (GitHub Pages or another static host).  
3. **Documentation hygiene**:  
   - When changing any subsystem, update the relevant README plus this overview if paths or capabilities change.

By following the links above, even contributors unfamiliar with the codebase can navigate the stack, run the apps locally, and understand how the LLM services plug into the RAG workflow.
