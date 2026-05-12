# Agentic Query

`agentic_query` is a scoped RAG runtime that uses DeepSeek through an OpenAI-compatible chat-completions endpoint.

The runtime now uses progressive skill loading:
- `config/system.md` is the only always-loaded system prompt.
- `config/skills/**/SKILL.md` frontmatter is indexed at startup as compact skill metadata.
- Full skill bodies are loaded only when the assistant calls `load_answering_instructions`.
- Skill references under `references/` are optional and loaded only through `read_skill_reference`.
- Python enforces tool validation, retrieval scope, citation normalization, loop limits, and timeout fallback.

## Structure

```text
config/
  system.md
  skills/
    agentic_query/
      SKILL.md
      references/
        answer_examples.md
        retrieval_refinement_examples.md
```

Legacy files such as `config/skills.md` and `config/references/citation_policy.md` are deprecated and unused by the new runtime path.

## Runtime Flow

1. `run_agentic_query(...)` loads `config/system.md`.
2. The config loader scans `config/skills/**/SKILL.md` and parses YAML frontmatter only.
3. The assistant receives a persistent `messages[]` transcript containing:
   - system base prompt
   - compact skill registry metadata
   - user query and bounded runtime state
4. The assistant returns one JSON action-state object.
5. The runtime appends that object as an `assistant` message, validates it, executes the tool, and appends the result as a `tool` message.
6. The loop continues until `provide_final_answer`, max steps, or timeout.

The transcript is the primary continuity mechanism. A compact structured step trace is also kept for logging, progress events, and bounded state updates.

## JSON Action Protocol

DeepSeek is called using normal chat completions. Native provider tool calls are not required; the assistant emits strict JSON:

```json
{
  "intent": "short operational sentence",
  "action": "load_answering_instructions|find_files_by_name|search_relevant_chunks|read_chunk_detail|read_file_chunks|read_skill_reference|provide_final_answer",
  "arguments": {},
  "success_criteria": "short condition for sufficiency",
  "fallback": "short next step if insufficient"
}
```

Tool argument shapes:

```json
{"action":"load_answering_instructions","arguments":{"skill_name":"agentic-query"}}
{"action":"find_files_by_name","arguments":{"query":"filename terms","limit":5}}
{"action":"search_relevant_chunks","arguments":{"query":"string","top_k":8}}
{"action":"read_chunk_detail","arguments":{"parent_id":"string"}}
{"action":"read_file_chunks","arguments":{"file_id":"string","file_name":"optional string","max_chunks":20}}
{"action":"read_skill_reference","arguments":{"skill_name":"agentic-query","ref_id":"answer_examples"}}
{"action":"provide_final_answer","arguments":{"answer":"string","citations":["file_name"]}}
```

## Tools

- `load_answering_instructions`: returns the full body of a registered skill and caches it for the run.
- `find_files_by_name`: finds scoped files by filename and returns file IDs.
- `search_relevant_chunks`: performs scoped semantic retrieval and returns relevant parent chunk snippets.
- `read_chunk_detail`: loads a larger bounded view of one scoped parent chunk by ID.
- `read_file_chunks`: reads ordered parent chunks from one scoped file.
- `read_skill_reference`: loads a bounded optional reference for a specific skill.
- `provide_final_answer`: returns the final answer and file-name citations.

The only registered skill for now is `agentic-query`. The architecture is intentionally small but can support more skills by adding another `config/skills/<name>/SKILL.md`.

## Safety and Termination

- Retrieval tools enforce user ownership and file scope.
- `top_k`, snippets, skill bodies, and references are bounded.
- Citations are normalized to observed in-scope file names.
- Invalid actions are logged and the loop continues.
- Max-step fallback forces one final no-tool `provide_final_answer` attempt using the accumulated transcript.
- Hard timeout returns `No answer found in the provided context.`

## Environment

`llm_client.py` resolves DeepSeek/OpenAI-compatible settings in this order:

- URL: `AGENTIC_QUERY_LLM_URL` -> `MOD_AGENT_LLM_URL` -> `https://api.deepseek.com/v1/chat/completions`
- API key: `AGENTIC_QUERY_LLM_KEY` -> `MOD_AGENT_LLM_KEY`
- Model: `AGENTIC_QUERY_LLM_MODEL` -> `MOD_AGENT_LLM_MODEL` -> `deepseek-chat`

## Debugging

Debug logs are written to `backend/debug/logs/agentic_query_debug.txt`.

Logs include:
- loaded skill registry metadata
- confirmation that skill bodies were not preloaded
- full skill loads
- reference loads
- assistant action summaries
- tool arguments and results
- termination reason

