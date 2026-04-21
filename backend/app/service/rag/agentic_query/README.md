# Agentic Query (v1) - Markdown-First Hybrid

## Overview
This package implements an agentic retrieval runtime for RAG queries.

The design is intentionally split:
- Markdown (`config/skills.md`, optional `config/references/*.md`) defines behavior policy.
- Python runtime enforces tool execution, scoping, validation, loop limits, and stop conditions.

This keeps prompt context small while still allowing flexible agent behavior.

## Package Structure
- `runtime.py`: bounded action loop, tool dispatch, stop logic, progress events.
- `tools.py`: scoped retrieval tools (`search_context`, `fetch_parent_chunk`, `read_reference`).
- `models.py`: typed action/result schemas.
- `llm_client.py`: OpenAI-compatible chat-completions caller.
- `config_loader.py`: loads/validates `skills.md` and lazily indexed references.
- `config/skills.md`: system policy prompt injected each turn.
- `config/references/*.md`: optional policy docs only loaded by `read_reference`.

## End-to-End Flow
1. API route (`/api/agent/query` or `/api/agent/query-stream`) calls `run_agentic_query(...)`.
2. Runtime loads cached config (`skills.md` + reference index).
3. Runtime does seed retrieval (`search_context_tool`) with `seed_top_k` to ground step 1.
4. For each step `1..max_steps`, runtime sends:
   - system prompt: `skills.md`
   - user prompt: query + evidence summary + recent observations + JSON schema
5. Model must return exactly one JSON action:
   - `search_context`
   - `fetch_parent_chunk`
   - `read_reference`
   - `finish`
   - with optional trace fields: `intent`, `decision`
6. Runtime validates action and arguments via Pydantic, executes tool, appends observations, and repeats.
7. On `finish`, runtime normalizes citations to observed in-scope file names and returns.

## Action Protocol
Expected model shape per turn:

```json
{"action":"search_context|fetch_parent_chunk|read_reference|finish","arguments":{...},"intent":"optional short string","decision":"optional short string"}
```

Action argument shapes:

```json
{"action":"search_context","arguments":{"query":"string","top_k":8},"intent":"Find direct evidence","decision":"If weak, narrow query"}
{"action":"fetch_parent_chunk","arguments":{"parent_id":"string"},"intent":"Inspect one chunk deeply","decision":"If irrelevant, return to search"}
{"action":"read_reference","arguments":{"ref_id":"citation_policy"},"intent":"Confirm citation rule","decision":"Continue with same evidence"}
{"action":"finish","arguments":{"answer":"string","citations":["file_name.pdf"]},"intent":"Enough evidence collected","decision":"Stop"}
```

## Structured Step Trace
The runtime keeps a bounded, structured in-run trace for continuity:
- `intent` (model-provided, optional)
- `action` + `arguments` (model-provided)
- `observation` (runtime-generated from tool result)
- `decision` (model-provided, optional)

This trace is:
- fed back into subsequent turns (`Recent Structured Step Trace`),
- streamed to frontend progress events,
- written to `agentic_query_debug.txt`.

## Stop Conditions
The runtime stops in one of these ways:
- `finished`: model returned valid `finish`.
- `forced_finish_after_max_steps`: no finish in loop; runtime forces one final no-tool synthesis turn.
- `max_steps_exceeded`: forced finish failed; runtime returns fallback no-answer text.
- `timeout`: hard wall-clock timeout from `asyncio.wait_for(...)`.

## Markdown-First Context Behavior
- `skills.md` is always loaded as the system prompt.
- Reference markdown is **not** injected by default.
- A reference is loaded only when model emits `read_reference`.
- All reads are logged, so you can verify if references were actually used.

## Scope and Safety Rules
- Retrieval tools enforce user ownership and collection/file scoping.
- `top_k` and snippet sizes are capped in code.
- Citations are normalized to allowed file names observed during tool calls.
- Invalid/malformed actions are ignored for that turn and logged, then loop continues.

## Streaming Progress
When using `/api/agent/query-stream`, runtime emits progress payloads with:
- `stage`
- `status`
- `message`
- optional `metadata` (`runId`, step counters, counts)

Typical stages:
- `agentic_query_pipeline`
- `agentic_query_seed`
- `agentic_query_step`

## Debug Logging
Runtime debug output is written to:
- `backend/debug/logs/agentic_query_debug.txt`

Important log event groups:
- `AGENTIC QUERY LLM REQUEST` (full prompts)
- `AGENTIC QUERY LLM RESPONSE` (raw model output)
- `AGENTIC QUERY ACTION` (parsed action + args + result/errors)
- `AGENTIC QUERY CONFIG EVENT` (skills/references usage, termination metadata)

Use these logs to inspect:
- which tools were called and with what arguments,
- whether `skills.md` was used,
- whether any reference docs were read,
- why the run terminated.

## Environment Variables
`llm_client.py` resolves config in this order:
- URL: `AGENTIC_QUERY_LLM_URL` -> `MOD_AGENT_LLM_URL` -> default DeepSeek URL
- API key: `AGENTIC_QUERY_LLM_KEY` -> `MOD_AGENT_LLM_KEY`
- Model: `AGENTIC_QUERY_LLM_MODEL` -> `MOD_AGENT_LLM_MODEL` -> `deepseek-chat`

## Extending v1
To add a new tool safely:
1. Add tool name to `models.AgentAction` literal.
2. Add argument model in `models.py`.
3. Implement tool in `tools.py` with scoping/limits.
4. Add dispatch branch in `runtime.py`.
5. Update `skills.md` action guidance.
6. Add tests for validation, scoping, and stop behavior.

Keep executable logic in Python; keep policy guidance in markdown.
