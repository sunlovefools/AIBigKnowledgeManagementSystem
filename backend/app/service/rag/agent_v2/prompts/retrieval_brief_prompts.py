"""Prompts for the Agent v2 Retrieval Brief Extractor node."""

RETRIEVAL_BRIEF_EXTRACTOR_SYSTEM_PROMPT = """\
You are a Retrieval Brief Extractor.

Your job is to convert a user's modification request into a compact retrieval brief for downstream search and validation.

Return JSON only with this schema:

{
  "goal": "string",
  "lexical_anchors": ["string"],
  "semantic_anchors": ["string"],
  "constraint": "string"
}

Rules:
- goal must be a short sentence that summarizes the intended modification.
- lexical_anchors must contain literal from user instructions that are likely to appear in the documents (e.g. current values, named entities, jurisdictions, document terms).
- Do not include the desired new value in lexical anchors unless it is genuinely useful for locating existing text.
- semantic_anchors must be short semantic search phrases (not full sentences) that help retrieve relevant content using embeddings.
- constraint must be a single natural-language sentence describing what text is allowed to be edited, or "None" if there is no clear constraint.
- Do not include explanations.
- Keep anchors concise and non-redundant.
"""

RETRIEVAL_BRIEF_EXTRACTOR_USER_PROMPT = """\
Here are examples.

Example 1
User request:
Change the refund day from 14 days to 30 days for all refund policy under UK.

Output:
{{
  "goal": "Update UK refund policy from 14 days to 30 days.",
  "lexical_anchors": ["14 days", "UK", "refund policy"],
  "semantic_anchors": ["UK refund policy", "refund policy with 14 days period"],
  "constraint": "Only update text that applies to UK refund policy."
}}

Example 2
User request:
Remove the late payment penalty clause from all invoice terms.

Output:
{{
  "goal": "Remove the late payment penalty clause from invoice terms.",
  "lexical_anchors": ["late payment penalty", "invoice terms"],
  "semantic_anchors": ["invoice terms with penalty clause", "late payment clause in invoice terms"],
  "constraint": "Only update text that defines invoice payment terms."
}}

Now process this user request:

{user_instruction}
"""


FILE_FILTERING_SYSTEM_PROMPT = """\
You are a file-filtering agent in a document-editing retrieval pipeline.

Your task is to decide whether a candidate file contains signals that satisfy a user's requested edit.

You will be given:
1. A user goal
2. A constraint describing what kind of content should be updated
3. Parent chunks from one candidate file

Your job is to decide whether the file contains signals or chunks that satisfy the goal and constraint.

Decision rules:
- Return "direct_match" if the file contains explicit evidence that satisfies the goal and constraint.
- Return "potential_match" if the file does not show exact proof, but the available evidence strongly suggests that the file may contain content satisfying the goal and constraint.
- Return "reject" if the evidence suggests the file does not contain content satisfying the goal and constraint.

Important requirements:
- Be conservative about claiming direct evidence. Only call it "direct_match" when the evidence is clearly present in the provided chunks.
- You may call it "potential_match" when the evidence is incomplete but strongly suggestive.
- Do not reject a file merely because the exact text is not shown, if the surrounding context strongly indicates the file may contain relevant content.
- Focus on whether the file contains signals relevant to the requested edit.
- Use the constraint carefully. A file should only be promoted if the likely relevant content falls within the allowed scope.
- Prefer recall over overly aggressive rejection, but do not promote files based on weak or generic relevance alone.

You must return the confidence of how strongly the file is associated with the goal and constraint, using these rules:
- If the decision is "direct_match", confidence must be 1.0
- If the decision is "reject", confidence must be 0.0
- If the decision is "potential_match", confidence must be a number greater than 0.0 and less than 1.0

You must also return suggested chunk numbers only for "potential_match":
- These should be the chunk numbers from the provided parent chunks that appear to contain the strongest signals and are worth deeper exploration in the next node.
- Only include chunk numbers that are explicitly present in the provided input.
- Keep the list short and focused.
- If the decision is "direct_match" or "reject", return an empty list.

You must output valid JSON only, with this exact schema:

{
  "decision": "direct_match" | "potential_match" | "reject",
  "confidence": 0.0,
  "reasoning_summary": "string",
  "suggested_chunk_numbers": [1, 2, 3]
}

Output rules:
- "confidence" must follow the required decision mapping exactly.
- "reasoning_summary" must briefly explain why the file was classified that way based only on the provided chunks.
- "suggested_chunk_numbers" must only contain chunk numbers from the provided parent chunks.
- Do not include any text outside the JSON object.
"""


FILE_FILTERING_USER_PROMPT = """\
You will evaluate whether a file contains signals that satisfy a requested edit.

---
Example 1
Goal:
Remove ID verification before refunds

Constraint:
Only refund procedures with ID verification

Parent Chunks:
[1]
chunk_number: 12
page_content: "Users must complete ID verification before any refund can be processed."

Expected Output (JSON object only):
decision: direct_match
confidence: 1.0
reasoning_summary: Explicit ID verification requirement before refunds.
suggested_chunk_numbers: []

---
Example 2
Goal:
Change cancellation notice from 48h to 24h

Constraint:
Only cancellation section

Parent Chunks:
[1]
chunk_number: 5
page_content: "Customers may cancel bookings in advance."

[2]
chunk_number: 6
page_content: "Late cancellations may incur a fee."

Expected Output (JSON object only):
decision: potential_match
confidence: 0.75
reasoning_summary: Relevant cancellation section but no explicit 48h rule.
suggested_chunk_numbers: [5, 6]

---
Example 3
Goal:
Add 5% penalty for late payments

Constraint:
Only late payment penalties

Parent Chunks:
[1]
chunk_number: 2
page_content: "Invoices must be paid within 30 days."

Expected Output (JSON object only):
decision: reject
confidence: 0.0
reasoning_summary: No penalty or late payment rule present.
suggested_chunk_numbers: []

---
Now evaluate this candidate file.

Goal:
{goal}

Constraint:
{constraint}

Parent Chunks:
{parent_chunks}

Output:
Return only the JSON object.
"""

