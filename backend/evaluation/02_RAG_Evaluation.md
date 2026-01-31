# RAG Pipeline Evaluation with Ragas

This step evaluates the RAG pipeline using **RAGAS** metrics against the Golden Dataset produced in `ingest_test_dataset.py` script.

## Script
- **File:** `evaluate_rag.py`

## Overview
**Ragas** (Retrieval Augmented Generation Assessment) is a framework that helps evaluate the performance of RAG pipelines by using a "Judge LLM" to rigorously score the quality of your system's retrieval and generation.

It evaluates the pipeline component-by-component, checking both:

1. **Retrieval:** Did we find the right documents?
2. **Generation:** Did the LLM answer the question accurately and faithfully based on those documents?

## Required Services
To successfully run this evaluation, several services must be active. If any of these are down, the script will fail.
- **Embeddings:** used by RAGAS to compute semantic similarity for metrics.
- **LLM (Judge):** used by RAGAS to grade answer quality and grounding.
- **Answer Generator LLM:** your RAG pipeline's LLM service that generates answers.

The script uses a **local LLM endpoint** (configured in `evaluate_rag.py`). Ensure the local LLM service is running **before** you start the evaluation.

## The 5 RAGAS Metrics
The evaluation uses the following RAGAS metrics:
1. **Faithfulness**  
    - Measures whether the generated answer is supported by the retrieved context (groundedness).
2. **Answer Relevancy**  
    - Checks how relevant the answer is to the question (focus and completeness based on the user query).
3. **Answer Correctness**  
   - Compares the generated answer to the ground-truth answer for factual correctness.
4. **Context Precision**  
   - Measures how much of the retrieved context is relevant to the question (signal vs noise).
5. **Context Recall**  
   - Measures whether the retrieved context contains all the information needed to answer correctly.

Together, these metrics evaluate both **retrieval quality** (context precision/recall) and **generation quality** (faithfulness, relevancy, correctness).

# Evaluation Process
The `evaluate_rag.py` script performs two main tasks sequentially:

1. Generation Phase:
    - It iterates through the Golden Dataset (`wikieval_golden_dataset.json`).
    - For each question, it queries your actual RAG pipeline to retrieve context and generate an answer.
    - It records the Generated Answer, Retrieved Contexts, and Ground Truth.

2. Evaluation Phase:
    - It passes these recorded results to the Ragas Judge (your local LLM).
    - The Judge calculates the 5 metrics for every single row.
    - Finally, it aggregates the scores.

## How to Run
```bash
python evaluate_rag.py
```

## Output
- **CSV Report:** `backend/evaluation/ragas_evaluation_results.csv` which contains per-row metric scores
- **Console Summary:** aggregated metric scores

## More Information about each Metric

### Faithfulness
In Faithfulness, RAGAS first uses the LLM to split the responses (From the generated answer) into a list of standalone statements. Then, for each statement, it uses the LLM to check if it is supported by the retrieved context. A statement is supported will receive a score of 1, otherwise 0. The final faithfulness score is the average of these scores across all statements.

faithfulness = (Number of Supported Statements [Statement with score 1]) / (Total Number of Statements)

More details can be found here:
https://github.com/vibrantlabsai/ragas/blob/main/src/ragas/metrics/_faithfulness.py

### Answer Relevancy
