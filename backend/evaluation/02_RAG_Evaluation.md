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
- **LLM (Judge):** used by RAGAS to grade answer quality and grounding. [Make sure to run Models/Evaluate_Judge_LLM/main.py first]
- **Answer Generator LLM:** your RAG pipeline's LLM service that generates answers. [Make sure either Models/Local_Answer_Generator/main.py or the Beam-hosted service is running]

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
In Answer Relevancy, RAGAS first ask the LLM to generate a question based on the generated answer. In the same time, it will ask the LLM to mark whether the generated answer is noncommital (i.e. does not provide a specific answer) or commital (i.e. provides a specific answer). Then, the a similarity score is computed between the original question and the generated question using embedding cosine similarity and take the mean similarity score as the final relevancy score. However, if the generated answer is marked as noncommital, the final relevancy score will be 0.

More details can be found here:
https://github.com/vibrantlabsai/ragas/blob/main/src/ragas/metrics/_answer_relevance.py

### Answer Correctness
In Answer Correctness, RAGAS first first convert the generated answer and ground truth into atomic statements using the LLM. Then, each atomic statement in the generated answer will be put into one of the following categories by comparing with the ground truth statements using the LLM:
- **TP (True Positive):** The statement is correct and is present in the ground truth.
- **FP (False Positive):** The statement is incorrect and is not present in the ground truth.
- **FN (False Negative):** The statement is correct but is not present in the ground truth.

After that, it will count the total number of TP, FP and FN statements and compute the precision and recall as follows:
- **Precision** = TP / (TP + FP)
- **Recall** = TP / (TP + FN)

Finally , a factuality score is computed using the formula below:

- **Factuality Score** = 2 * (Precision * Recall) / (Precision + Recall)

Independently, the ground truth and generated answer are also compared using embedding cosine similarity to compute a similarity score. The final answer correctness score is as follows:

- **Answer Correctness Score** = (Factuality Score * 0.75 + Similarity Score * 0.25)

where the Factuality Score is weighted more heavily to prioritise factual accuracy over semantic similarity.

More details can be found here:
https://github.com/vibrantlabsai/ragas/blob/main/src/ragas/metrics/_answer_correctness.py

### Context Precision
In Context Precision, RAGAS will analyze each chunks of the retrieved context using the LLM to determine if it is relevant to answering the question given the answer. 

"Given (question, ground_truth, context chunk), is this context chunk relevant to answering the question?"

such that the LLM will output verdict = 1 if the context chunk is relevant, otherwise verdict = 0.

Then, RAGAS will convert the verdict list into Average Precision score.

<details add="worked_example"> <summary><strong>Worked example: Average Precision (AP)</strong></summary>

Assume the retriever returns 5 contexts in ranked order.
After LLM verification, each context is labelled useful (1) or not useful (0):

Verdicts (rank 1 → 5):
[1, 0, 1, 1, 0]

We compute Average Precision (AP) by adding Precision@i each time we see a useful context (verdict = 1).

Rank 1: verdict = 1
Relevant so far = 1
Precision@1 = 1/1 = 1.00 → add 1.00

Rank 2: verdict = 0
Relevant so far = 1
No contribution (only add when verdict = 1)

Rank 3: verdict = 1
Relevant so far = 2
Precision@3 = 2/3 = 0.6667 → add 0.6667

Rank 4: verdict = 1
Relevant so far = 3
Precision@4 = 3/4 = 0.75 → add 0.75

Rank 5: verdict = 0
Relevant so far = 3
No contribution

Sum of precisions at relevant ranks:
1.00 + 0.6667 + 0.75 = 2.4167

Number of relevant contexts:
3

𝐴𝑃 = 2.4167 / 3 = 0.8056

So, context_precision ≈ 0.806.

</details>

<br>

More details can be found here:
https://github.com/vibrantlabsai/ragas/blob/main/src/ragas/metrics/_context_precision.py

### Context Recall
In Context Recall, RAGAS will first extract all the factual statements from the ground truth answer using the LLM. Then, for each factual statement, it will check if the statement is present in any of the retrieved context chunks using the LLM. Such that for each factual statement, if it is found in any of the retrieved context chunks, it will receive a score of 1, otherwise 0. The final context recall score is as follows:

**context_recall_score** = (Number of Found Statements [Statement with score 1]) / (Total Number of Factual Statements)

More details can be found here:
https://github.com/vibrantlabsai/ragas/blob/main/src/ragas/metrics/_context_recall.py