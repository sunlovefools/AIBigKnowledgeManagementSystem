---
name: agentic-query
description: Answer scoped RAG questions by searching allowed context, inspecting parent chunks when needed, and finishing with file-name citations only.
allowed-tools:
  - load_answering_instructions
  - find_files_by_name
  - find_inventory_records
  - search_relevant_chunks
  - read_chunk_detail
  - read_file_chunks
  - read_skill_reference
  - provide_final_answer
---

# Agentic Query

## Purpose
Use this skill to answer factual questions using only scoped retrieved evidence.

## Goal
Find the best answer from the available evidence and finish with valid file-name citations only.

## Working method
1. Start with `search_relevant_chunks` to gather relevant evidence.
2. Use `find_inventory_records` when the user asks for all, every, or listed items; semantic search is ranked and is not a complete inventory.
3. When an inventory query also asks for details per item, keep every inventory item in the answer. Retrieve missing item details with `read_file_chunks` or `read_chunk_detail` when needed; if a detail is still not observed for an item, say it was not found in the provided context for that item rather than omitting the item.
4. Use `find_files_by_name` when the user names, implies, or asks about a whole file and the filename/file ID is uncertain.
5. Use `read_file_chunks` when the user asks to summarize, audit, compare, or otherwise answer from an entire file.
6. If `find_files_by_name` finds a likely named target file and its content is still unread, strongly consider `read_file_chunks` before continuing broad search.
7. Use `read_chunk_detail` when a specific parent chunk needs deeper inspection.
8. Use `read_skill_reference` only when optional examples or guidance would help.
9. Avoid repeating the same failed search.
10. Finish as soon as the evidence is sufficient.

## Tool behavior
- `search_relevant_chunks` is a semantic search tool. It returns focused snippets from relevant parent chunks, not a complete file.
- `find_inventory_records` scans scoped files for records matching exhaustive list-style queries. Use it for inventories instead of relying only on semantic top-k results.
- `read_chunk_detail` returns a larger bounded view of one known parent chunk. Use it when a search result is promising but the snippet is too short.
- `find_files_by_name` finds file IDs by filename. It does not read the whole file.
- `read_file_chunks` reads ordered parent chunks from a selected file. Use it for whole-file questions. The runtime still returns bounded snippets per chunk so the agent does not overflow its context window.
- Snippets are evidence previews, not proof that the file is inaccessible. If the user asks about an entire file or the current snippet is insufficient, call `read_file_chunks` or `read_chunk_detail` instead of guessing.

## Database mental model
- User data is scoped first by authenticated user, then optionally by collection.
- Collections contain files. Collection-scoped runs only expose file IDs that belong to the active collection; all-collection runs expose all files owned by the user.
- Files are split into ordered parent chunks. Parent chunks are the main answer evidence and include `parent_id`, `file_id`, `file_name`, `parent_chunk_number`, page metadata, and linked child chunk IDs.
- Child chunks are smaller embedding records used for semantic matching. Search results are lifted back to parent chunks so you can answer from a wider source block.
- For table-heavy documents, parent chunks may include a `structured_view` with row labels, fields, weights, and key values. Prefer this structure over flattening everything into prose.

## Citation rules
- Cite file names only.
- Cite only files that directly support the final answer.
- Do not fabricate citations.
- If evidence is insufficient, finish with exactly: `No answer found in the provided context.`

## Search refinement guidance
- Narrow the query if the results are relevant but too broad.
- Broaden the query if results are sparse, empty, or off-target.
- Prefer the smallest number of searches needed to reach a supported answer.
- For exhaustive list/inventory questions, prefer `find_inventory_records` over repeated semantic searches.
- For module inventories filtered by a semester/term, include full-year or year-long modules for both Autumn and Spring unless the user explicitly excludes them.
- For exhaustive inventory questions with attached attributes, such as module assessment weights, use inventory results as the completeness baseline. Semantic search may supply attributes for only a subset, but it must not define the item list.
- If `search_relevant_chunks` does not find relevant content within 2-3 attempts, switch to `find_files_by_name` to locate a named file directly.
- If initial search reveals the target file but not all needed chunks, call `read_file_chunks` using the observed `file_id`.
- Inspect a parent chunk only when the chunk identity is already known and deeper confirmation is needed.

## Finish rules
- The final answer must be plain text.
- The final answer must be supported by observed evidence.
- Use the smallest sufficient citation set.
- If the answer is not supported, do not guess.
- When the user asks for lists, requirements, criteria, components, fields, prices, steps, categories, or other itemized facts, preserve the item/row structure from the evidence.
- If the evidence exposes an inventory of items and only some items have observed detail fields, include all inventory items and mark the unobserved details as not found in the provided context.
- If the evidence includes table rows or a structured table view, answer from the relevant rows/fields and keep sibling rows distinct. Include weights, amounts, dates, labels, or other key values when present and relevant.
- Do not replace item-level evidence with a broad summary unless the user explicitly asks for a summary.
