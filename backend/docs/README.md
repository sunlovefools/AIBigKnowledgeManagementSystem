# Backend Docs Index

**Navigation:** [Backend guide](../BACKEND_README.md) | [App overview](../app/README.md) | [API routers](../app/api/README.md) | [Service layer](../app/service/README.md) | [Tests](../tests/README.md)

This folder contains deep dives for backend features. Use it as a “how it works” companion to the code.

## Documents
- `ingestion.md`
  - **Non-technical:** Explains what happens when you upload a file and how we slice it up.
  - **Technical:** Webhook payloads, chunking parameters, and local test commands.
- `query.md`
  - **Non-technical:** Explains how a question is refined and answered using stored documents.
  - **Technical:** RAG pipeline details, router flow, limitations, and model calls.
- `Vector_DB.md`
  - **Non-technical:** Describes the “card catalog” where chunks are stored for search.
  - **Technical:** Schema, embedding dimensions, and Astra collection behavior.

Use these alongside the linked READMEs to trace requests end to end. Update this index when adding new backend docs.
