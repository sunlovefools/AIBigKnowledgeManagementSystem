---
name: document-modification
description: Explore scoped document chunks, identify all affected files and locations, and generate reviewable parent-chunk edit proposals.
allowed-tools:
  - load_skill
  - search_files
  - search_context
  - fetch_file_outline
  - fetch_parent_chunk
  - fetch_chunk_window
  - delegate_file_edits
  - read_reference
  - finish
---

# Document Modification

## Purpose
Use this skill to generate reviewable edit proposals for documents stored as ordered parent chunks.

## Goal
Find every in-scope file and parent chunk that should change, then return parent-level proposals. The user will review and apply proposals later.

## Working Method
1. Search for likely files and content using `search_context`.
2. Use `search_files` when the user names or implies a file but the file ID is uncertain.
3. Use `fetch_file_outline` to understand a file without loading the full document.
4. Use `fetch_chunk_window` when a chunk needs surrounding context.
5. Use `fetch_parent_chunk` when exact original text is needed.
6. Use `delegate_file_edits` for one or more candidate files. This is the preferred editing path because it runs file-scoped workers in parallel.
7. If a discovered file is not edited, include a clear skip reason.
8. Finish only when all observed candidate files are edited, delegated, or explicitly skipped.

## Editing Rules
- Preserve unrelated content.
- Apply all requested changes inside the same parent chunk in one proposal.
- Do not split one parent chunk into multiple proposals.
- Do not invent facts.
- If the instruction cannot be safely applied to a chunk, leave it unchanged and skip it.

## Output Rules
- Proposals must use exact parent chunk originals observed through tools.
- Each proposal must include the full original parent chunk text and the full proposed replacement text.
- Do not persist changes.
