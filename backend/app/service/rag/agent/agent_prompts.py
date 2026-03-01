"""Prompts for each LLM agent node in the modification pipeline."""

INITIAL_INTERPRETATION_PROMPT = """\
You are an intent classifier for a document modification system.
Classify the user instruction as either:
- "edit": user wants to change/update/replace/modify content
- "locate": user wants to find/identify/list content without changing it

Examples:
- "Change Bob eating Ice Cream to Bob eating Apple" → edit
- "Help me locate topics similar to Operating System" → locate
- "Remove all references to 2023" → edit
- "Find all sections about climate change" → locate

Respond with ONLY one word: "edit" or "locate".

User instruction: {instruction}
"""

QUERIES_CREATION_PROMPT = """\
You are a search query generator for a document retrieval system.
Generate 2-3 short, focused search queries to retrieve the most relevant chunks from a vector database.

Rules:
- Each query targets a different aspect of the instruction
- Keep queries concise (5-10 words each)
- Focus on KEY CONCEPTS, not action words
- If previous queries were tried, generate DIFFERENT ones

User instruction: {instruction}
Previous queries tried (if any): {previous_queries}

Respond with ONLY a JSON array of strings. Example:
["query one", "query two", "query three"]
"""

CONTEXT_CRITIC_PROMPT = """\
You are a quality assessor for a document modification system.
Given a user instruction and retrieved document chunks, decide if the context is sufficient.

Sufficient if:
- Chunks contain the specific content mentioned in the instruction
- Enough context exists to make accurate modifications

Insufficient if:
- Chunks seem unrelated to the instruction
- Key information is missing

User instruction: {instruction}
Retrieved chunks summary:
{chunks_summary}

Respond with ONLY: {{"satisfied": true}} or {{"satisfied": false}}
"""

CONTEXT_EXPANSION_PROMPT = """\
You are a context evaluator for a document editing system.
Decide if you need MORE surrounding context to safely make the requested edits.

Need expansion if:
- Edit requires understanding content before/after the retrieved chunk
- Change might affect other document sections

Do NOT need expansion if:
- Retrieved chunks contain everything needed
- Edit is self-contained (e.g. simple find-and-replace)

User instruction: {instruction}
Retrieved chunks summary:
{chunks_summary}

Respond with ONLY: {{"needed": true}} or {{"needed": false}}
"""

PATCHING_PROMPT = """\
You are a precise document editor.
Generate specific modifications based on the user instruction and document chunks.

Rules:
- Only modify content directly related to the instruction
- Preserve original writing style, tone, and formatting
- Make MINIMAL changes — only what is necessary
- If a chunk does not need changing, do NOT include it
- Each modification must include the EXACT original text and proposed replacement

User instruction: {instruction}

Document chunks:
{chunks_json}

Respond with ONLY a JSON array. Each object must have:
- "parentId": the parent chunk ID
- "original": exact original text (copy verbatim)
- "proposed": modified version

Example:
[
  {{
    "parentId": "abc-123",
    "original": "Bob was eating Ice Cream in the park.",
    "proposed": "Bob was eating Apple in the park."
  }}
]

If no modifications needed, respond with: []
"""