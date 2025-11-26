# Feature Documentation Hub

[Feature index](./README.md) | [Authentication](./authentication.md) | [Ingestion pipeline](./ingestion-pipeline.md) | [Query & RAG](./query-pipeline.md) | [Frontend register](./frontend-register.md) | [Frontend workspace](./frontend-workspace.md)

Use this hub to jump into the detailed READMEs for each feature in the AI Knowledge Management platform. Every feature page links back here and to the other pages so you can move quickly between backend and frontend flows.

## Feature list
- [Authentication](./authentication.md) - user signup/login API with validation, hashing, and AstraDB storage.
- [Ingestion pipeline](./ingestion-pipeline.md) - upload handling, text extraction, chunking, embeddings, and vector upserts.
- [Query & RAG](./query-pipeline.md) - refined querying, similarity search, and answer generation.
- [Frontend register](./frontend-register.md) - registration UX and Axios wiring to `/auth/register`.
- [Frontend workspace](./frontend-workspace.md) - chat UI, document uploads, and query calls from the main page.

## How to use these docs
1) Start with the feature you are working on, then follow the links in the nav bar to see how it interacts with the adjacent layers.
2) Each page lists the exact code entry points and API contracts so you can trace a request end to end.
3) Keep this hub updated if you add new features or rename files so the cross-links stay valid.
