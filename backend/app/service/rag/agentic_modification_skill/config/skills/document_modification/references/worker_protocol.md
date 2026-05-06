# Worker Protocol

File workers operate on one file at a time.

1. Inspect the file outline.
2. Select every parent chunk that may need editing.
3. Inspect local windows for those chunks.
4. Return one proposal per changed parent chunk.
5. If no chunk should change, return a skip reason.

Workers should prefer recall over premature rejection. The final proposal step can leave a candidate unchanged if the exact chunk content does not support the edit.
