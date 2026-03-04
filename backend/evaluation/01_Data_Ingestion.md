# Data Ingestion For RAG Evaluation (WikiEval)

This step prepares the evaluation dataset and ingests its contexts into the Vector DB so the RAG pipeline can be evaluated against a known ground truth.

## Script
- **File:** `ingest_test_dataset.py`

## Overview
The ingestion script performs two critical functions:

1. **Golden Dataset Creation**: Downloads the raw WikiEval dataset and standardizes it into a local JSON file.
2. **Vector DB Ingestion**: Simulates the application's ingestion pipeline by processing these records (chunking, polishing, embedding) and uploading them to AstraDB.

### Golden Dataset Creation
The script first downloads the WikiEval dataset (`vibrantlabsai/WikiEval`) from Hugging Face. It then transforms the raw data into a structured **"Golden Dataset"** format required for our evaluation logic.

Structure of each entry is formatted as:

```JSON
{
    "answer": ["..."], // Ground truth answer
    "question": ["..."], // User query
    "context_v2": ["..."], // List of context paragraphs
    "row_number": "row x" // x is an integer based on the original dataset indexing
}
```
Output: The structured data is saved to `evaluation/data/wikieval_golden_dataset.json`.

### Vector DB Ingestion
Once the golden dataset is ready, the script iterates through every row and ingests it into AstraDB. Crucially, it reuses the actual application logic to ingest the data

For each row in the dataset:

1. File Simulation: The script treats the `context_v2` list as a single file named `wikieval_row_X.txt`.

2. Parent/Child Chunking:
    - Uses `split_parent_child_chunks` from the main app
3. Chunk Polishing:
    - Applies `polish_chunks` to child chunks to improve retrieval quality before embedding.
4. Upsert:
    - Uses `upsert_documents` to generate embeddings and store both parent and child chunks in AstraDB.

## Usage Instructions
You can run the ingestion script via the command line but make sure you are at `/backend/evaluation/` path:

```bash
python ingest_test_dataset.py
```

### When to Run
Run this once to bootstrap evaluation data **only if the Vector DB does not already contain the WikiEval contexts**.

Do **not** rerun if the collection already has these documents, unless you explicitly want to overwrite or re-upsert them.

## Dataset Overview (WikiEval)
The evaluation uses the WikiEval dataset, specifically designed to benchmark RAG pipelines

- **Source:** https://huggingface.co/datasets/vibrantlabsai/WikiEval

- **Description:** This is a dataset for to do correlation analysis of difference metrics proposed in Ragas. This dataset was generated from 50 pages from Wikipedia with edits post 2022 hence there are only in total 50 rows.

- **Schema used:**
  - `question`: the user query
  - `answer`: the ground-truth answer
  - `context_v2`: the document context to be ingested
