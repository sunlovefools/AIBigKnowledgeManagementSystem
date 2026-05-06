"""
Prompt templates for semantic table ingestion.
"""

from __future__ import annotations


CLASSIFIER_SYSTEM_PROMPT = """
You are an expert data architect.

Analyze a parsed table sample and surrounding context.

Classify the table as exactly one of:
- layout: text-heavy grid used for readability/definitions; relational semantics are weak.
- matrix: rows and columns are jointly required to interpret values.
- entity_list: each row is an independent entity/record.

Decide whether semantic description extraction is needed:
- true for matrix/entity_list
- false for layout

Return ONLY valid JSON:
{
  "type": "layout | matrix | entity_list",
  "needs_description": true,
  "col_headers": ["..."],
  "row_headers": ["..."]
}
""".strip()


DESCRIPTION_AND_SECTIONS_SYSTEM_PROMPT = """
You are an expert technical writer and table structure analyst.

Analyze a parsed table sample and return:
- a concise 1-2 sentence description of what the table represents
- whether the table has logical section groups
- clean section names and clean row labels when section groups exist

Rules:
- Do not summarize specific cell values in the description; describe the table purpose.
- A "section header row" is a body row that introduces a logical group or category. Rows that follow it until the next section header belong to that section.
- A valid section structure requires at least 2 distinct sections.
- If the table is a flat list with no grouping, return has_sections: false.
- Use zero-based row_index values for body rows only. Do not count the markdown header or separator rows.
- Section names must be short and clean (for example, "Curriculum Vitae", not "Curriculum Vitae Total: ...").
- Distinguish aggregate labels from real section boundaries. Rows whose meaningful cells are only totals, subtotals, scores, percentages, or aggregate labels are summary rows, not section headers.
- If a row has a label such as "Total" but also introduces a new substantive topic with criteria/items in other cells, it may be a section boundary; clean the section name by removing aggregate suffixes such as "Total" or "Total:".
- Do not merge adjacent topics into one section name. If a row appears to contain the end of one topic and the start of another, choose the start row of the new topic and return only the new clean topic name.
- row_labels must be short clean labels only, not full criteria descriptions. For example, use "Motivation", not "Motivation (20%) Criteria: ...".
- If a section contains meaningful sub-sections or criteria rows that should be retrieved separately, include them in subsections.

Return ONLY valid JSON:
{
  "description": "<1-2 sentence table description>",
  "has_sections": true | false,
  "sections": [
    {
      "row_index": <int>,
      "section_name": "<string>",
      "row_labels": ["..."],
      "subsections": [
        {"row_index": <int>, "section_name": "<string>", "row_labels": ["..."]}
      ]
    }
  ]
}
""".strip()


ROW_SUMMARY_SYSTEM_PROMPT = """
You are a precise data analyst specialized in generating retrieval-optimized summaries from tabular data.

Core Task:
- Produce exactly ONE summary per slice.

Summary Requirements:
- Each summary must be EXACTLY one sentence (≤ 35 words).
- Focus on:
  • key entities (e.g., regions, endpoints, products)
  • important dimensions (time, category, metrics, fields)
  • relationships or comparisons across columns
  • trend direction ONLY if clearly supported by values
- Optimize for retrieval:
  • include meaningful keywords (entities + dimensions)
  • avoid vague phrasing ("this slice shows data")

Output Format (STRICT):
- Return ONLY a valid JSON array
- Each item must EXACTLY match:
  {"slice_index": <int>, "summary": "<string>"}
""".strip()


def build_classifier_user_prompt(
    *,
    context_before: str,
    table_markdown_sample: str,
    context_after: str,
) -> str:
    """
    Build the user prompt for classifying the table type and whether a global description is needed, incorporating a markdown sample of the table and its surrounding context to inform the model's understanding of the table's structure and purpose.
    """
    return (
        f"Context Before:\n{context_before}\n\n"
        f"Table Sample (first rows):\n{table_markdown_sample}\n\n"
        f"Context After:\n{context_after}\n\n"
        "Analyze this table and output the JSON classification."
    )


def build_description_and_sections_user_prompt(
    *,
    col_headers: list[str],
    row_headers: list[str],
    context_before: str,
    context_after: str,
    table_sample_markdown: str,
) -> str:
    """Build the combined table description + section detection prompt."""
    return (
        f"Column Headers: {col_headers}\n"
        f"Row Headers / Index: {row_headers}\n"
        f"Context Before: {context_before}\n"
        f"Context After: {context_after}\n\n"
        "Markdown table sample:\n"
        f"{table_sample_markdown}\n\n"
        "Write the general table description and identify section header rows. "
        "Return JSON only."
    )


def build_row_summary_user_prompt(
    *,
    general_description: str,
    explicit_slices_markdown: str,
    slice_size: int,
    expected_slice_count: int,
) -> str:
    """
    Build the user prompt for summarizing explicit markdown row slices into
    retrieval-friendly slice summaries.

    The model is asked to:
    - use only the provided explicit slices,
    - preserve the provided slice boundaries,
    - summarize each slice in order,
    - return only a JSON array.
    """
    return f"""Generate one summary per provided slice.

Example 1:
Input:
Global Table Description:
Quarterly regional revenue by business unit.
Slice Size: 2
Expected Slice Count: 2
Explicit Slices:
Slice 0:
| Region | Q1 2024 | Q2 2024 |
| --- | --- | --- |
| North America | 15.2 | 16.5 |
| EMEA | 10.1 | 11.0 |

Slice 1:
| Region | Q1 2024 | Q2 2024 |
| --- | --- | --- |
| APAC | 5.4 | 6.2 |
| LATAM | 3.1 | 3.4 |

Output:
[
  {{"slice_index": 0, "summary": "North America and EMEA rows compare regional revenue across Q1 and Q2 2024, highlighting early-year performance by major markets."}},
  {{"slice_index": 1, "summary": "APAC and LATAM rows compare Q1 and Q2 2024 revenue for smaller regional markets in the same reporting period."}}
]

Example 2:
Input:
Global Table Description:
API endpoint catalog with methods and limits.
Slice Size: 3
Expected Slice Count: 2
Explicit Slices:
Slice 0:
| Endpoint | Method | Description | Rate Limit |
| --- | --- | --- | --- |
| /api/v1/users | GET | List users | 100/min |
| /api/v1/users/{{id}} | GET | Get user details | 300/min |
| /api/v1/auth/login | POST | Authenticate user | 20/min |

Slice 1:
| Endpoint | Method | Description | Rate Limit |
| --- | --- | --- | --- |
| /api/v1/orders | GET | List orders | 120/min |

Output:
[
  {{"slice_index": 0, "summary": "This slice covers user listing, user detail retrieval, and login endpoints, combining read and authentication operations with differing rate limits."}},
  {{"slice_index": 1, "summary": "This slice contains the order-list retrieval endpoint and its associated method, purpose, and access limit."}}
]

Now process the actual input.
Global Table Description:
{general_description}
Slice Size:{slice_size}
Expected Slice Count:{expected_slice_count}
Explicit Slices:
{explicit_slices_markdown}

Output:
"""
