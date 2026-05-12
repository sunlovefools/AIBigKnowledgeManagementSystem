# Retrieval Refinement Examples

These are optional examples for deciding how to adjust retrieval.

## Case 1: results are broad but relevant
Observed situation:
The search returns many related results, but they cover the whole project rather than the exact topic.

Preferred move:
Run a narrower `search_relevant_chunks` query using the exact topic, policy term, or entity name.

## Case 2: results are sparse
Observed situation:
The search returns no useful evidence or only weakly related snippets.

Preferred move:
Run a broader `search_relevant_chunks` query with fewer constraints or more general wording.

## Case 3: one chunk looks promising
Observed situation:
A parent chunk appears directly relevant and likely contains the exact statement needed.

Preferred move:
Use `read_chunk_detail` for that parent chunk before running another search.

## Case 4: repeated failed search
Observed situation:
A similar query has already failed and produced no better evidence.

Preferred move:
Do not repeat the same failed query. Change the query strategy or finish if evidence remains insufficient.

## Case 5: enough evidence already exists
Observed situation:
The current evidence cache already answers the question directly.

Preferred move:
Finish instead of continuing to search.
