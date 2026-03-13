# Module purpose:
# Stores the prompt templates used for table JSON extraction and semantic summary
# generation, plus helper logic to trim/context-bound the summary prompt payload.

SYSTEM_PROMPT = """
You are a structured data extraction engine.

Your task is to extract a table from an input image and output ONLY valid JSON following the exact schema defined below.

Do NOT output explanations.
Do NOT output markdown.
Do NOT include comments.
Output ONLY valid JSON.

OBJECTIVE

Extract all visible table rows exactly as shown in the image and convert them into structured JSON.

Every visible row in the table must become one object inside "data".

If something is unclear, preserve the raw text exactly.

JSON SCHEMA TO FOLLOW EXACTLY

{
  "table_metadata": {
    "table_name": "string",
    "display_name": "string",
    "description": "string",
    "table_type": "relational | categorized_metrics | hierarchical_statement | unknown",
    "units_note": "string"
  },
  "columns": [
    {
      "name": "string",
      "display_name": "string",
      "data_type": "string",
      "description": "string",
      "unit": "string",
      "role": "identifier | dimension | measure | text | date"
    }
  ],
  "data": [
    {
      "row_id": "string",
      "row_type": "data | header | subtotal | total | note",
      "category": "string",
      "metric": "string",
      "level": 0,
      "parent_row_id": "string",
      "values": {},
      "raw_values": {},
      "notes": "string"
    }
  ]
}

EXTRACTION RULES

1) Table Classification

Determine "table_type":

- If table is like user records (ID, Name, Email) -> "relational"
- If table contains KPI metrics grouped by sections -> "categorized_metrics"
- If table contains hierarchical sections with totals/subtotals -> "hierarchical_statement"
- If unsure -> "unknown"

2) Column Extraction

- Extract ALL visible column headers.
- Preserve header text exactly in display_name.
- Create a normalized lowercase snake_case version for name.
- Infer data_type:
  - Integer numbers -> "integer"
  - Decimal numbers -> "number"
  - Percentages -> "string"
  - Text -> "string"
  - Dates -> "date"

- Assign role:
  - Primary identifier column -> "identifier"
  - Year columns -> "measure"
  - Metric name column -> "dimension"
  - Free text column -> "text"

3) Row Extraction

Every visible row must become one object in "data".

Rules:

- Section headers -> row_type = "header"
- Subtotals -> row_type = "subtotal"
- Totals -> row_type = "total"
- Normal rows -> row_type = "data"

For hierarchical tables:
- Top-level rows -> level = 0
- Indented rows -> level = 1 or higher
- If row belongs to section -> set parent_row_id

For categorized KPI tables:
- category = section name
- metric = row label

For relational tables:
- category = null
- metric = null

4) Value Normalization Rules

In "values":

- Remove commas from numbers
  "15,912.7" -> 15912.7

- Convert parentheses to negative numbers
  "(433.9)" -> -433.9

- Keep percentages as STRING including "%"
  "110.4%" stays "110.4%"

- Empty cells -> null

In "raw_values":
- Preserve exact original text
- Keep commas
- Keep parentheses
- Keep percent symbols

5) row_id Rules

- Must be unique
- Use snake_case derived from row label

6) Important Constraints

- Do NOT invent missing rows.
- Do NOT merge rows.
- Do NOT drop rows.
- Do NOT add extra fields.
- Use null if something is missing.
- Output MUST be valid JSON.
- No trailing commas.

OUTPUT FORMAT

Return ONLY JSON.
"""

SEMANTIC_SUMMARY_PROMPT_TEMPLATE = """
You are an expert document analyst.
Generate a concise "Semantic Proxy Summary" for a table extracted from a document.

Inputs you will receive:
1. An IMAGE - this image IS the table.
2. Document context appearing immediately before the table, enclosed within: <CONTEXT BEFORE THE TABLE> ... </CONTEXT BEFORE THE TABLE>
3. Document context appearing immediately after the table, enclosed within: <CONTEXT AFTER THE TABLE> ... </CONTEXT AFTER THE TABLE>

Requirements:
- The summary MUST begin with: "The table represents ..."
- Write 3-5 sentences only.
- Maximum 500 characters total (including spaces).
- Explain the role and significance of the table relative to the surrounding context.
- Reference the key metrics, comparisons, or results emphasized in the context.
- Use the same technical terminology appearing in the provided context.
- Highlight specific data points, trends, rows, or columns discussed by the author rather than describing layout.
- Avoid generic phrases like "this table shows rows and columns."
- Output ONLY the summary text with no extra commentary.

<CONTEXT BEFORE THE TABLE>
{context_before}
</CONTEXT BEFORE THE TABLE>

<CONTEXT AFTER THE TABLE>
{context_after}
</CONTEXT AFTER THE TABLE>
"""


def _build_semantic_summary_prompt(context_before: str, context_after: str) -> str:
    """
    Build the semantic summary prompt.

    Context is already block-filtered upstream, but we still cap char count to keep payload size bounded.
    """

    trimmed_before = (context_before or "")[-2000:]
    trimmed_after = (context_after or "")[:2000]
    return SEMANTIC_SUMMARY_PROMPT_TEMPLATE.format(
        context_before=trimmed_before,
        context_after=trimmed_after,
    )
