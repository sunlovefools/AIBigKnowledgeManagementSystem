# Backend Tests

**Navigation:** [Backend guide](../BACKEND_README.md) | [App overview](../app/README.md) | [API routers](../app/api/README.md) | [Service layer](../app/service/README.md) | [Docs](../docs/README.md)

This folder holds unit and integration tests for ingestion, query, and auth components. Tests are our “early warning system” that catches regressions.

## Key files
- `test_latest.py`
  - **Non-technical:** Confirms sign-up/login validation still behaves.
  - **Technical:** Covers auth validation and error cases.
- `test_text_extractor.py`, `test_chunker.py`, `test_chunk_polisher.py`
  - **Non-technical:** Ensure we can read files and slice text reliably.
  - **Technical:** Unit tests for ingestion helpers.
- `test_ingest.py`, `test_all_ingest_modules.py`
  - **Non-technical:** Simulate a full upload and check outputs.
  - **Technical:** Integration-style checks across extraction → chunking → embeddings.
- `test_query_manual.py`
  - **Non-technical:** Spot-check the question/answer pipeline.
  - **Technical:** Manual assertions for query flow.

## Running tests
From `backend/`:
```
pytest
```
If imports require environment variables, set safe dummy Astra/Beam values in `.env` before running.

Add new tests alongside features and update this README so contributors know where coverage lives.
