---
name: agentic-query
description: Answer scoped RAG questions by searching allowed context, inspecting parent chunks when needed, and finishing with file-name citations only.
allowed-tools:
  - load_skill
  - search_files
  - search_context
  - fetch_parent_chunk
  - fetch_file_context
  - read_reference
  - finish
---

# Agentic Query

## Purpose
Use this skill to answer factual questions using only scoped retrieved evidence.

## Goal
Find the best answer from the available evidence and finish with valid file-name citations only.

## Working method
1. Start with `search_context` to gather relevant evidence.
2. Use `search_files` when the user names, implies, or asks about a whole file and the filename/file ID is uncertain.
3. Use `fetch_file_context` when the user asks to summarize, audit, compare, or otherwise answer from an entire file.
4. If `search_files` finds a likely named target file and its content is still unread, strongly consider `fetch_file_context` before continuing broad search.
5. Use `fetch_parent_chunk` when a specific parent chunk needs deeper inspection.
6. Use `read_reference` only when optional examples or guidance would help.
7. Avoid repeating the same failed search.
8. Finish as soon as the evidence is sufficient.

## Citation rules
- Cite file names only.
- Cite only files that directly support the final answer.
- Do not fabricate citations.
- If evidence is insufficient, finish with exactly: `No answer found in the provided context.`

## Search refinement guidance
- Narrow the query if the results are relevant but too broad.
- Broaden the query if results are sparse, empty, or off-target.
- Prefer the smallest number of searches needed to reach a supported answer.
- If `search_context` does not find relevant content within 2-3 attempts, switch to `search_files` to locate a named file directly.
- If initial search reveals the target file but not all needed chunks, call `fetch_file_context` using the observed `file_id`.
- Inspect a parent chunk only when the chunk identity is already known and deeper confirmation is needed.

## Finish rules
- The final answer must be plain text.
- The final answer must be supported by observed evidence.
- Use the smallest sufficient citation set.
- If the answer is not supported, do not guess.
- When the user asks for lists, requirements, criteria, components, fields, prices, steps, categories, or other itemized facts, preserve the item/row structure from the evidence.
- If the evidence includes table rows or a structured table view, answer from the relevant rows/fields and keep sibling rows distinct. Include weights, amounts, dates, labels, or other key values when present and relevant.
- Do not replace item-level evidence with a broad summary unless the user explicitly asks for a summary.
