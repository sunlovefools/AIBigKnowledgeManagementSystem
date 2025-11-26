# Backend App Overview

**Navigation:** [API routers](./api/README.md) | [Service layer](./service/README.md) | [Core utilities](./core/README.md) | [Backend guide](../BACKEND_README.md) | [Docs](../docs/README.md) | [Tests](../tests/README.md)

This is the heart of the backend application. If the backend were a house, `main.py` is the front door and electrical panel—it decides which room (router) a request should go to and wires everything together.

## Plain-language + technical map
- `main.py`  
  - **Non-technical:** Starts the server and tells it which feature areas exist.  
  - **Technical:** Creates the FastAPI app, loads environment variables, adds CORS, registers routers, and triggers AstraDB init on startup.
- `api/`  
  - **Non-technical:** The front doors: URLs for sign up/login, file upload, and question answering.  
  - **Technical:** FastAPI routers with Pydantic models for request/response validation.
- `service/`  
  - **Non-technical:** The “do the work” room that actually processes files and questions.  
  - **Technical:** Business logic for auth, ingestion, embeddings, vector search, and answer generation.
- `core/`  
  - **Non-technical:** Shared toolkit for safe inputs and secure passwords.  
  - **Technical:** Validation helpers and bcrypt hashing utilities imported by services.

## How a request moves through
1) A user action (e.g., click “register” or “upload”) hits a URL defined in `api/`.  
2) The router checks the incoming data and hands it to the right `service/` function.  
3) Services use `core/` helpers plus external systems (Astra DB, Beam) to finish the job.  
4) The result is packaged and sent back to the user through the router.

Non-technical readers: think of `api/` as customer service, `service/` as operations, and `core/` as shared tools. Developers: follow the linked READMEs for code-level details and module entry points.
