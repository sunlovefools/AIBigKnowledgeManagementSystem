# Backend Folder Overview

**Navigation:** [App code](./app/README.md) | [Docs](./docs/README.md) | [Tests](./tests/README.md) | [Project README](../README.md) | [Models](../Models/README.md) | [Frontend doc](../frontend/FRONTEND_README.md)

This is the server that powers the product. It handles sign ups, file uploads, and question answering. Think of it as the “engine room” that receives requests from the website, does the heavy lifting, and replies with results.

## What’s inside (non-technical + technical)
- `app/` — the running FastAPI app.  
  - **Non-technical:** The place where incoming requests are routed to the right feature (auth, upload, query).  
  - **Technical:** Contains routers, services, middleware, and startup hooks that initialize AstraDB and Beam clients.
- `docs/` — deep dives for each feature.  
  - **Non-technical:** Read these to understand what happens when you upload a file or ask a question.  
  - **Technical:** Sequence diagrams, payload shapes, and troubleshooting notes.
- `tests/` — automated safety net.  
  - **Non-technical:** Lets us know if we broke something after changes.  
  - **Technical:** Pytest suites for auth, ingestion, and query; can be run locally or in CI.
- `BACKEND_README.md` — setup + architecture.  
  - **Non-technical:** Step-by-step run instructions.  
  - **Technical:** Environment variables, Docker usage, and dependency list.
- Supporting files (`requirements.txt`, `Dockerfile`, `.env`)  
  - **Non-technical:** “Ingredients list” and “kitchen setup.”  
  - **Technical:** Pin Python packages, build container images, and define secrets/URLs.

If you’re non-technical, skim `BACKEND_README.md` first, then open the `docs/` files for the feature you care about. Developers should start in `app/` and follow the links in the nested READMEs to find the exact code paths.
