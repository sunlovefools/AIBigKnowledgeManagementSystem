# Reranker Package

## Overview
This package provides a model-agnostic reranking layer for RAG retrieval.

Callers use one public service (`ZeRankerService`) and do not need to know
whether the backend is BGE or Qwen. Backend selection is controlled by
`RERANKER_MODEL` in `.env`.

## Architecture
- **Facade/orchestration**: `service.py`
- **Backend interface**: `interface.py`
- **Concrete backends**:
  - `bge_reranker.py`
  - `qwen_reranker.py`
- **Shared helpers**: `utils.py`
- **Public exports**: `__init__.py`

The flow is:
1. `ZeRankerService` reads env config and selects backend.
2. Backend scores `[query, document]` pairs through the common interface.
3. Service handles retries, device movement, and optional swap-to-RAM.
4. Service sorts by score and returns top-k pairs.

## Interface Contract
`RerankerInterface` defines the methods all backends must implement:

- `set_device(target_device: str) -> None`
  - Move model weights to target device (`cpu`, `cuda`, `mps`).
- `score_query_documents(query_documents: list[list[str]]) -> list[float]`
  - Return one score per input pair (same order as input).
- `force_batch_size_one() -> bool`
  - Used by service retry path to reduce batching for padding-related errors.
- `supports_runtime_padding_retry(error_text: str) -> bool`
  - Return `True` if an error can be recovered by shrinking batch size.

Any new reranker backend must satisfy this contract.

## Module Details

### `service.py`
Responsibility:
- public entrypoint (`ZeRankerService`)
- environment/model validation and fallback
- runtime orchestration:
  - async lock for inference/device safety
  - single retry with `batch_size=1` for known recoverable errors
  - fail-soft fallback (original order with score `0.0`)
  - optional GPU offload via `RERANKER_SWAP_TO_RAM`

Public constants:
- `DEFAULT_RERANKER_MODEL`
- `SUPPORTED_RERANKER_MODELS`

### `bge_reranker.py`
Implements BGE reranking via `sentence_transformers.CrossEncoder`.

Behavior:
- loads model and runs warmup predict
- ensures tokenizer has padding token (or forces batch size 1)
- scores with `CrossEncoder.predict`

### `qwen_reranker.py`
Implements Qwen3 instruction-aware reranking via
`transformers.AutoTokenizer` + `AutoModelForCausalLM`.

Prompt format:
```text
<Instruct>: {instruction}
<Query>: {query}
<Document>: {document}
```

Scoring:
- compute final-token logits
- relevance score = `logit("yes") - logit("no")`

### `utils.py`
Reusable helpers:
- env bool parsing
- preferred device detection
- accelerator cache clearing
- tokenizer padding normalization
- tensor-like to Python list conversion

### `__init__.py`
Exports package-level API:
- `ZeRankerService`
- `RerankerInterface`
- `QWEN_RERANK_INSTRUCTION`
- model config constants

## Configuration
- `RERANKER_MODEL`
  - `BAAI/bge-reranker-v2-m3`
  - `Qwen/Qwen3-Reranker-0.6B`
- `RERANKER_SWAP_TO_RAM`
  - truthy values: `1`, `true`, `yes`, `on`

Note: model changes require backend restart.

## Extending with a New Backend
1. Create new module (e.g., `my_reranker.py`) implementing `RerankerInterface`.
2. Add it to `SUPPORTED_RERANKER_MODELS` in `service.py` with:
   - `backend` label
   - `factory`
   - backend kwargs
3. Add tests that validate:
   - scoring behavior
   - retry behavior (if applicable)
   - device offload integration via `ZeRankerService`.
