# Chunk Construction Rules (Ingestion and Modification)

This document is the implementation-level reference for how chunks are built today.

Source code of truth:
- `backend/app/api/router_ingest.py`
- `backend/app/service/rag/ingestion/chunker.py`
- `backend/app/service/rag/ingestion/docling_chunker.py`
- `backend/app/service/rag/ingestion/markdown_chunker.py`
- `backend/app/service/rag/ingestion/markdown_canonicalizer.py`
- `backend/app/service/modification/reconstruction_service.py`

## 1. Which chunker runs in each path

### Ingestion (`POST /ingest/upload`)
- Non-PDF files: always `legacy` chunker (`split_parent_child_chunks`).
- PDF files:
1. If `INGEST_PDF_EXTRACTOR=legacy`: legacy chunker.
2. If `INGEST_PDF_EXTRACTOR=docling`: Docling block chunker (`split_parent_child_chunks_from_docling_blocks`).

### Modification / re-chunking
All modification flows use markdown chunker (`split_parent_child_chunks_from_markdown`):
- single parent update (`update_document`)
- full file update (`update_file`)
- batch parent updates (`fast_updates`)
- boundary re-chunk (`boundary_rechunk`)

## 2. Shared text normalization behavior

### Ingestion normalization
- Legacy path canonicalizes extracted text first with `canonicalize_markdown_text`.
- Docling path canonicalizes each Docling block via `canonicalize_docling_block_text` during block coercion.
- Before upsert, both ingestion paths run `canonicalize_chunk_payloads_for_storage` on both parent and child content.

### Modification normalization
- Modification paths normalize editor text with `normalize_markdown_for_modification` before chunking.
- They do not call `canonicalize_chunk_payloads_for_storage` afterward.

## 3. Legacy ingestion chunking (`chunker.py`)

This path is character-based, not word-based, and does not build sections.

### Parameters used from router
- `parent_target_chars=1500`
- `child_max_chars=600`
- `min_parent_chars=900` (function default)
- `min_child_chars=50` (function default)

### Step A: initial child split
`_create_initial_child_chunks` uses `RecursiveCharacterTextSplitter`:
- `chunk_size = child_max_chars`
- `chunk_overlap = 10%` of `chunk_size`
- separators (priority order): `"\n\n"`, `"\n"`, `"."`, `" "`, `""`

### Step B: merge tiny child chunks
`_merge_small_chunks` merges children smaller than `min_child_chars`:
- tiny non-last chunk: buffered and prepended to next chunk
- tiny trailing buffer at end: appended to last merged chunk
- result: no dropped text, but tiny chunk boundaries are removed

### Step C: group children into parents
`_group_children_into_parents` accumulates child chunks by char length:
- when adding next child would exceed `parent_target_chars`, start new parent group
- after grouping, if the final group is below `min_parent_chars` and there is a previous group, merge final into previous

### Step D: model creation
- one `file_id` generated once and shared by all parent/child chunks
- each parent gets new `parent_chunk_id`
- each child gets new `child_chunk_id`, with `child_chunk_metadata.parent_id` pointing to parent
- `parent_chunk_metadata.child_chunks_ids` records the linked children

### Step E: post-chunk child polish (legacy ingestion only)
In router:
- child chunks are passed through `polish_chunks` (whitespace/punctuation/casing cleanup)
- parent chunks are not polished

### Legacy edge cases
- empty canonicalized text => no parent/child chunks
- very short input can produce 1 parent with 1 child
- overlap from recursive splitter is preserved unless later tiny-merge combines chunks

## 4. Docling ingestion chunking (`docling_chunker.py`)

This path is block-aware and word-based.

### Parameters (defaults used by router)
- `parent_max_words=500`
- `child_max_words=80`
- `min_child_words=20`
- `context_words=20`

### Step A: block coercion (`_coerce_blocks`)
For each incoming structured block:
- validate into `DoclingStructuredBlock`
- canonicalize block content by block type
- drop block if canonicalized content is empty
- preserve original block order

### Step B: section construction (`_build_sections`)
Rules:
1. If no header exists: one intro section (`has_header=False`, preamble empty, body all blocks).
2. Blocks before first header become intro section.
3. Header starts a new section preamble.
4. Consecutive headers are grouped into the same preamble until body appears.
5. Non-header blocks after preamble become section body.

### Step C: split section into parent parts (`_split_section_into_parent_parts`)
Rules:
- splitting keeps whole blocks intact (no parent-level block slicing here)
- first parent part includes preamble + body blocks
- later parts include body blocks only (preamble not repeated in parent content)
- if section has preamble but no body, one part is produced from preamble

Important consequence:
- a single oversized body block can create a parent part larger than `parent_max_words`

### Step D: child candidate creation per block (`_build_child_candidates_for_block`)
- `picture` block:
1. Build child text from picture marker + nearest text/list context (prefer previous, else next).
2. Extract `image_uuid` marker from content.
3. Mark `content_flags.is_image=true`.

- `table` block:
1. Always one child (never split by `child_max_words`).
2. Child text = previous context + middle table content + next context.
3. If `is_table_image=true`, middle content is VLM summary text (not raw table-image markdown) with fallback summary text when missing.
4. Mark `content_flags.is_table_image` and carry `table_image_uuid`.

- `text`, `list`, `header` blocks:
1. If word count > `child_max_words`, split by sentence regex `(?<=[.!?])\s+`.
2. If sentence splitting is not possible (single sentence/no punctuation), keep as one chunk.

### Step E: merge tiny children (`_merge_small_children`)
Rules:
- only within a single parent part
- tiny threshold applies to body-derived child content before preamble injection
- visual children (image/table-image) are never merged with text children
- tiny leading text before a visual child becomes its own non-visual child

### Step F: preamble injection into children (`_prefix_children_with_preamble`)
If preamble exists, it is prefixed to every child content in that parent part.

`has_preamble` metadata behavior:
- first parent part: first child may have prefixed preamble but `has_preamble=false`
- first parent part: later children have `has_preamble=true`
- later parent parts: all children have `has_preamble=true`

### Step G: fallback child
If merged child list is empty but parent content exists, one fallback child is created with full parent content.

### Step H: parent and child metadata
- parent chunk numbers are sequential by produced parent part order
- child chunk numbers are global sequence across entire file
- parent page numbers are normalized and sorted; synthetic page `0` is dropped when real page numbers exist
- parent-level `content_flags`/`artifact_refs` are aggregated from children

### Docling edge cases
- empty/fully-dropped block list => no chunks
- intro section is created when content appears before first header
- back-to-back headers stay in same preamble
- table children ignore `child_max_words` by design

## 5. Modification chunking (`markdown_chunker.py`)

This path is markdown-text aware and word-based.

### Parameters used by modification service
- `parent_max_words=500`
- `child_max_words=80`
- `min_child_words=20`

### Step A: tokenize markdown into blocks (`_tokenize_markdown_blocks`)
Rules:
- split into paragraph-like blocks on blank lines
- detect ATX headers (`#` to `######`) as `header` blocks
- preserve fenced code blocks as single blocks (no splitting inside fences)
- ignore empty blocks

### Step B: section construction (`_build_sections`)
Same section rules as Docling chunker:
- intro section before first header
- consecutive headers grouped into one preamble
- body follows preamble

### Step C: parent part construction (`_split_section_into_parent_parts`)
Difference from Docling:
- oversized `text`/`header` blocks are sentence-split first at parent stage
- then parent parts are packed by `parent_max_words`
- first part keeps preamble, later parts do not

Note:
- if a single sentence is longer than `parent_max_words`, it remains intact and parent can exceed limit

### Step D: child construction
For each block in a parent part:
- `text`/`header` over `child_max_words` are sentence-split
- others kept as one child text
- tiny child texts are merged by `_merge_small_children`

Merge behavior:
- tiny non-last chunk carries forward and prepends to next chunk
- trailing tiny carry is appended to previous merged child

### Step E: preamble injection into children
In markdown chunker, preamble is injected only when `not is_first_part`.

So:
- first parent part children do not get preamble prefix
- later parent parts children get preamble prefix and `has_preamble=true`

### Step F: fallback child
If no child texts remain for a parent, one fallback child is created from full parent content.

### Step G: post-chunk polish in modification service
After markdown chunking, modification service runs `polish_chunks` on child chunks before upsert.

## 6. Modification-specific re-chunk triggers and skips

### Single parent update (`update_document`)
- always re-chunks provided new content with markdown chunker
- deletes old parent + old children first

### Full file update (`update_file`)
- merges existing parent content in deterministic order
- normalizes both existing and incoming content
- if unchanged after normalization: skip delete/rechunk/upsert
- otherwise re-chunk full file content

### Batch `fast_updates`
- normalizes incoming per-parent updates
- re-chunks only changed parent IDs
- unchanged parent IDs are not re-chunked
- parent sequence is rebuilt and parent chunk numbers are re-assigned

### Batch `boundary_rechunk`
- normalizes full edited file content
- if unchanged from existing normalized merged content: skip re-chunk
- otherwise chunk full content, then diff old/new parent sequence
- delete/upsert only changed regions; preserved parents are re-numbered

## 7. Practical differences to remember

1. Legacy ingestion does not use section -> parent -> child; it is child-first char splitting then parent grouping.
2. Docling and modification chunkers are section-aware and word-based.
3. Docling child preamble behavior differs from markdown chunker behavior.
4. Docling has visual-aware child logic (picture/table contexts, table summary path); markdown chunker does not.
5. Ingestion always runs final canonical chunk payload normalization before upsert; modification does not.
