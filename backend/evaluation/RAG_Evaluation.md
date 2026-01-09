# RAG Evaluation & Data Ingestion

This document outlines the workflow for setting up the test data and running evaluations on our RAG pipeline.

## 1. The Dataset
For evaluation, we utilize the **WikiEval** dataset from Hugging Face:
* **Source:** [vibrantlabsai/WikiEval](https://huggingface.co/datasets/vibrantlabsai/WikiEval)
* **Composition:** The dataset consists of **50 rows** of QA pairs.
* **Origin:** It was generated from 50 Wikipedia pages containing information post-2022 (ensuring the data is recent and likely not in older base models).

## 2. Prerequisites
Before running the ingestion scripts, you must install the Hugging Face `datasets` library to download the raw data.

```bash
pip install datasets
```

Ensure your .env file is configured with the correct AstraDB and OpenAI credentials.

## 3. Dataset Ingestion (ingest_dataset.py)
We use the ingest_dataset.py script to bootstrap our environment for testing.

### Purpose
This script performs two critical tasks:

- Vector DB Ingestion: It simulates the ingestion pipeline by processing the dataset (chunking/polishing) and storing the contexts into the Vector Database (AstraDB).

- Golden Dataset Generation: It exports a structured JSON file (wikieval_golden_dataset.json) containing the Questions, Ground Truth Answers, and Contexts. This file is the reference standard used by Ragas to grade the system.

### How to Run
```bash

python ingest_dataset.py
```
⚠️ Important Note: If you have already run this script successfully, the data is already stored in your Vector Database. Do not run this script again unless you explicitly intend to overwrite or re-upsert the documents, as this may lead to redundant costs or data duplication depending on the upsert logic.

## 4. Running Evaluation (evaluate_rag)
(This section is currently under development. Instructions for running the Ragas metrics will be added here.)