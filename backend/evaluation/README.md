# RAG Pipeline Evaluation

This directory contains the tools and scripts required to ingest test data and evaluate the performance of our RAG pipeline using **Ragas**.

## 📋 Prerequisites

Before running any scripts, ensure your environment is set up:
1.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    Note that these dependancies are extra to those required for the main RAG pipeline.
    
2.  **Environment Variables:**
    Ensure your `.env` file (in the project root) is configured with:
    * `ASTRA_DB_URL` and `ASTRA_DB_TOKEN` for AstraDB access.
    * `BEAM_ANSWER_GENERATOR_LLM_URL` and `BEAM_ANSWER_GENERATOR_LLM_KEY` OR `LOCAL_ANSWER_GENERATOR_LLM_URL` and `LOCAL_ANSWER_GENERATOR_LLM_KEY` for the Beam-hosted answer generator LLM service.

## 🚀 Workflow Overview

To benchmark the system, follow these two steps in order. Click the links for detailed documentation:

### [Step 1: Data Ingestion](./01_Data_Ingestion.md)
* **Script:** `ingest_test_dataset.py`
* **Goal:** Downloads the *WikiEval* dataset, formats it into a "Golden Dataset," and ingests the documents into AstraDB.
* **Output:** `data/wikieval_golden_dataset.json`

### [Step 2: Run Evaluation](./02_RAG_Evaluation.md)
* **Script:** `evaluate_rag.py`
* **Goal:** Uses the Golden Dataset to query your RAG pipeline, generates answers, and scores them using Ragas metrics (Faithfulness, Precision, Recall, etc.).
* **Output:** Console report and `ragas_evaluation_results.csv`.