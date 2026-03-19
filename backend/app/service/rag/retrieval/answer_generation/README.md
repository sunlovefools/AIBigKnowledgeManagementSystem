# Answer Generation Package

## 1. Overview and Design Goals

The `answer_generation/` package refactors answer generation into focused modules so each concern can evolve independently without making the core file monolithic again.

Design goals:
- Preserve external compatibility with legacy imports through `retrieval/answer_generator.py`.
- Isolate provider-specific behavior (`Ollama`, `OpenRouter`) from orchestration.
- Keep env resolution and normalization logic deterministic and testable.
- Make extension (adding new providers) straightforward.

Non-goals:
- This package does not implement retrieval/search.
- This package does not alter prompt text semantics in `prompts/answer_generator_prompt.py`.

## 2. File Tree

```text
answer_generation/
  __init__.py
  models.py
  config.py
  context_normalizer.py
  citations.py
  http_client.py
  logging_adapter.py
  orchestration.py
  providers/
    __init__.py
    base_provider.py
    ollama_provider.py
    openrouter_provider.py
  README.md
```

Compatibility facade (outside this folder):
- `../answer_generator.py`

## 3. Module Function Catalog

### `models.py`

Purpose:
- Defines shared data contracts and constants.

Functions/Types:
- `ProviderName`:
  Input: N/A
  Output: `Literal["OLLAMA", "BEAM", "OPENROUTER"]`
  Responsibility: Canonical provider type values.
  Used by: Provider/config typing.
- `NormalizedMetadata` / `NormalizedRagDoc`:
  Responsibility: Typed normalized retrieval payload shape.
  Used by: normalizer + providers.
- `AnswerGeneratorConfig`:
  Responsibility: Runtime config envelope.
  Used by: config + orchestration + providers.
- `GenerationRequest`, `GenerationResult`:
  Responsibility: Optional typed request/result envelopes for future extension and testing.

### `config.py`

Purpose:
- Centralized environment parsing and URL pass-through handling.

Functions:
- `is_local_ollama_url(url: str | None) -> bool`
  Responsibility: Detect local daemon targets for SDK path routing.
  Called by: `providers/ollama_provider.py`.
- `load_answer_generator_config() -> AnswerGeneratorConfig`
  Responsibility: Parse env vars, enforce timeout validity, preserve provider/env precedence.
  Called by: `orchestration.py`.

### `context_normalizer.py`

Purpose:
- Transform retrieval output into strict provider input.

Functions:
- `extract_minimal_metadata(metadata: Any) -> NormalizedMetadata`
  Responsibility: Keep only `file_name` and `parent_chunk_number` for citations and traceability.
  Called by: `normalize_rag_docs()`.
- `normalize_rag_docs(rag_docs: list[dict[str, Any]]) -> list[NormalizedRagDoc]`
  Responsibility: Validate dict-only inputs and convert them into a consistent normalized shape.
  Called by: `orchestration.py`.
- `build_llm_context_docs(rag_docs: list[NormalizedRagDoc]) -> list[dict[str, str]]`
  Responsibility: Build reduced provider payload docs containing only `file_name` and `page_content`.
  Called by: `build_llm_context_payload()`.
- `build_llm_context_payload(rag_docs: list[NormalizedRagDoc]) -> str`
  Responsibility: Render numbered context blocks:
  `[n]`, `file_name: ...`, `page_content: "..."`.
  Called by: `providers/ollama_provider.py`, `providers/openrouter_provider.py`.

### `citations.py`

Purpose:
- Canonical source suffix handling.

Functions:
- `collect_source_file_names(rag_docs) -> list[str]`
  Responsibility: Ordered unique file-name extraction.
  Called by: `providers/openrouter_provider.py`.
- `format_sources_suffix(source_names) -> str`
  Responsibility: Deterministic suffix formatting.
  Called by: `append_or_replace_sources_suffix()`.
- `append_or_replace_sources_suffix(answer_text, source_names) -> str`
  Responsibility: Strip old suffix and append exactly one canonical suffix.
  Called by: `providers/openrouter_provider.py`.

### `http_client.py`

Purpose:
- Shared async POST helper.

Functions:
- `post_json(session, url, payload, headers, timeout_s, error_prefix) -> dict[str, Any]`
  Responsibility: Timeout/client/status/shape normalization.
  Called by: `providers/ollama_provider.py`, `providers/openrouter_provider.py`.

### `logging_adapter.py`

Purpose:
- Adapter for debug log integration with import fallback.

Functions:
- `log_llm_request(provider, model, user_query, rag_context_payload) -> None`
  Responsibility: Request payload debug logging.
  Called by: provider modules.
- `log_llm_response(answer) -> None`
  Responsibility: Response debug logging.
  Called by: provider modules.

### `providers/base_provider.py`

Purpose:
- Contract used by orchestration.

Functions/Types:
- `AnswerProvider` protocol:
  `async generate(session, cfg, rag_docs, user_query) -> str`
  Responsibility: Standard provider interface.
  Implemented by: `OllamaAnswerProvider`, `OpenRouterAnswerProvider`.

### `providers/ollama_provider.py`

Purpose:
- Ollama SDK + HTTP fallback implementation.

Functions:
- `coerce_ollama_response_dict(data, error_prefix) -> dict[str, Any]`
  Responsibility: Normalize SDK object/dict responses.
  Called by: `generate_via_ollama()`.
- `generate_via_ollama(session, cfg, rag_docs, user_query) -> str`
  Responsibility:
  - Validate model
  - Build numbered context-block payload
  - Build prompt
  - Route to SDK path for local URL
  - Fall back to HTTP path when SDK unavailable
  - Parse response and apply fallback text
  Called by: `OllamaAnswerProvider.generate()`.
- `OllamaAnswerProvider.generate(...)`
  Responsibility: Protocol adapter for orchestration.

### `providers/openrouter_provider.py`

Purpose:
- OpenRouter chat completion implementation.

Functions:
- `generate_via_openrouter(session, cfg, rag_docs, user_query) -> str`
  Responsibility:
  - Validate API key
  - Build numbered context-block payload
  - Build chat payload
  - Parse choices/message content
  - Apply canonical source suffix
  Called by: `OpenRouterAnswerProvider.generate()`.
- `OpenRouterAnswerProvider.generate(...)`
  Responsibility: Protocol adapter for orchestration.

### `orchestration.py`

Purpose:
- Single entrypoint that connects config, normalization, and provider execution.

Functions:
- `_resolve_provider(cfg) -> AnswerProvider`
  Responsibility: Map provider name to implementation class.
  Called by: `generate_answer()`.
- `generate_answer(rag_docs, user_query) -> str`
  Responsibility: Load config, normalize docs, open shared session, execute provider.
  Called by: compatibility facade and direct package imports.
- `generate_answer_api(rag_docs, user_query) -> str`
  Responsibility: historical wrapper compatibility.
## 4. End-to-End Answer Generation Flow

1. Request arrives at `[router_query.py](../../../../api/router_query.py)`.
2. Router calls retrieval in `[vectordb.py](../../../../vectordb/vectordb.py)` to obtain parent docs.
3. Router calls `generate_answer(...)` from compatibility facade `[answer_generator.py](../answer_generator.py)`.
4. Facade delegates to `answer_generation.orchestration.generate_answer(...)`.
5. `orchestration.py` loads env config (`config.load_answer_generator_config`).
6. `orchestration.py` normalizes retrieved docs (`context_normalizer.normalize_rag_docs`).
7. `orchestration.py` resolves provider (`_resolve_provider`).
8. Provider builds prompts using `[answer_generator_prompt.py](../prompts/answer_generator_prompt.py)`.
9. Provider calls remote/local model:
   - Ollama: SDK local path or HTTP fallback through `http_client.post_json`.
   - OpenRouter: chat-completions HTTP via `http_client.post_json` with numbered context blocks in the user message.
10. Provider parses response and logs request/response through `logging_adapter.py`.
11. OpenRouter path post-processes answer citations through `citations.py`.
12. Final answer string bubbles back through facade to API response.

## 5. Provider Decision Logic and Env Precedence

Provider routing:
- `OLLAMA` -> `OllamaAnswerProvider`
- `BEAM` -> `OllamaAnswerProvider` (compatibility alias)
- `OPENROUTER` -> `OpenRouterAnswerProvider`

Execution note:
- `OLLAMA` can use local SDK mode when targeting localhost.
- `BEAM` always uses HTTP mode so bearer authentication is consistently applied.

Env precedence highlights:

| Concern | Precedence |
|---|---|
| `provider` | `ANSWER_GENERATOR_LLM_PROVIDER` (default `OLLAMA`) |
| `timeout` | `ANSWER_GENERATOR_TIMEOUT_S` -> `ANSWER_GENERATOR_TIMEOUT` -> default `500.0` |
| `url` when provider=`BEAM` | `BEAM_ANSWER_GENERATOR_LLM_URL` (required) |
| `url` when provider=`OLLAMA` | `OLLAMA_ANSWER_GENERATOR_LLM_URL` (optional) |
| `url` when provider=`OPENROUTER` | `OPENROUTER_URL` -> default OpenRouter URL |
| `model` when provider=`OLLAMA`/`BEAM` | `OLLAMA_ANSWER_GENERATOR_LLM_MODEL` -> `LOCAL_ANSWER_GENERATOR_LLM_MODEL` -> `OLLAMA_MODEL` |
| `model` when provider=`OPENROUTER` | `OPENROUTER_MODEL` -> `deepseek/deepseek-r1:free` |
| `api_key` when provider=`BEAM` | `BEAM_ANSWER_GENERATOR_LLM_KEY` -> `LOCAL_ANSWER_GENERATOR_LLM_KEY` (required) |
| `api_key` when provider=`OPENROUTER` | `OPENROUTER_API_KEY` (required) |
| `api_key` when provider=`OLLAMA` | not required (local SDK path uses no key) |

URL format requirement:
- `OLLAMA_ANSWER_GENERATOR_LLM_URL` and `BEAM_ANSWER_GENERATOR_LLM_URL` should be full endpoint URLs (for example `http://127.0.0.1:11434/api/generate`) because URL path normalization is not applied.

## 6. Error Handling Map

- `config.py`
  - Invalid timeout parse -> `RuntimeError`.
  - Non-positive timeout -> `RuntimeError`.
- `context_normalizer.py`
  - Non-dict RAG item -> `RuntimeError` (strict input contract).
- `providers/ollama_provider.py`
  - Missing model -> `RuntimeError`.
  - SDK timeout -> `RuntimeError`.
  - SDK/coercion failures -> `RuntimeError`.
  - HTTP non-200/network/timeout -> normalized `RuntimeError` (via `http_client.py`).
- `providers/openrouter_provider.py`
  - Missing API key -> `RuntimeError`.
  - HTTP non-200/network/timeout -> normalized `RuntimeError`.
- `orchestration.py`
  - Unknown provider name -> `RuntimeError`.

## 7. Testing Strategy and Ownership

Recommended module ownership mapping:
- `test_answer_generation_config.py` -> `config.py`
- `test_answer_generation_context.py` -> `context_normalizer.py`
- `test_answer_generation_citations.py` -> `citations.py`
- `test_answer_generation_http.py` -> `http_client.py`
- `test_answer_generation_ollama_provider.py` -> `providers/ollama_provider.py`
- `test_answer_generation_openrouter_provider.py` -> `providers/openrouter_provider.py`
- `test_answer_generator_facade.py` -> `../answer_generator.py` compatibility wrappers

Testing principles:
- Unit test each module in isolation with monkeypatched dependencies.
- Avoid network calls in unit tests.
- Keep compatibility checks for legacy import paths.

## 8. Extension Guide: Add a New Provider

1. Create `providers/<new_provider>.py`.
2. Implement a class with `async generate(...) -> str` matching `AnswerProvider` protocol.
3. Reuse `http_client.post_json` and `logging_adapter` for consistency.
4. Register provider in `orchestration._resolve_provider`.
5. Add focused tests for parsing, validation, and fallback behavior.
6. Update this README provider table and env docs.
