import sys
import os
import json
import asyncio
import warnings # Import warnings to silence deprecation logs
import pandas as pd
from datasets import Dataset
from dotenv import load_dotenv

# 1. FIX: Use Legacy Imports (Lowercase)
# These allow custom/local embeddings without strict "Modern" checks
from ragas.metrics import (
    faithfulness,      # Lowercase = Legacy (Compatible)
    answer_relevancy,
    answer_correctness,
    context_precision,
    context_recall,
)

from openai import OpenAI
from ragas.llms import llm_factory
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.run_config import RunConfig

try:
    from langchain_openai import ChatOpenAI
except ImportError:
    from langchain_community.chat_models import ChatOpenAI

load_dotenv()

# ------------------------------------------------------------------
# 🔌 PATH SETUP
# ------------------------------------------------------------------
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

# Local imports
from app.vectordb.vectordb import search_and_retrieve_context
from app.service.rag.retrieval.answer_generator import generate_answer
from app.embedding.local_embedding_client import LocalGemmaEmbeddings 

# Filter warnings for cleaner output
warnings.filterwarnings("ignore", category=DeprecationWarning)

def clean_text(text):
    """Helper to remove newlines and flatten text for cleaner CSVs"""
    if isinstance(text, str):
        # Replace newlines with a distinct separator
        return text.replace('\n', ' | ').replace('\r', '')
    return text

async def generate_rag_responses(dataset_path: str):
    print(f"📂 Loading Golden Dataset from: {dataset_path}")
    
    with open(dataset_path, "r") as f:
        golden_data = json.load(f)
    
    golden_data = golden_data[:1]
    
    questions = []
    ground_truths = []
    generated_answers = []
    retrieved_contexts = []

    print(f"🚀 Starting RAG Generation for {len(golden_data)} test cases...\n")

    for i, item in enumerate(golden_data):
        q_val = item["question"]
        question = q_val[0] if isinstance(q_val, list) else q_val
        
        # Ensure Ground Truth is a STRING
        a_val = item["answer"]
        if isinstance(a_val, list):
            ground_truth = a_val[0] if len(a_val) > 0 else ""
        else:
            ground_truth = str(a_val)

        print(f"🔹 [{i+1}/{len(golden_data)}] Processing: {question[:50]}...")

        try:
            rag_contents = await search_and_retrieve_context(query=question, top_k=5)
            print(rag_contents)
            answer = await generate_answer_api(rag_contents, question)

            questions.append(question)
            ground_truths.append(ground_truth) 
            generated_answers.append(answer)
            retrieved_contexts.append(rag_contents)

        except Exception as e:
            print(f"❌ Error processing row {i}: {e}")
            questions.append(question)
            ground_truths.append(ground_truth)
            generated_answers.append("Error generating answer")
            retrieved_contexts.append(["Error retrieving context"])

    data_dict = {
        "question": questions,
        "answer": generated_answers,
        "contexts": retrieved_contexts,
        "ground_truth": ground_truths
    }
    
    return data_dict

def run_evaluation(data_dict):
    print("\n📊 Preparing Data for RAGAS Evaluation...")
    
    dataset = Dataset.from_dict(data_dict)

    print("🤖 Connecting to Judge LLM & Embeddings...")
    
    # ------------------------------------------------------------------
    # SETUP MODELS (Standard Wrapper approach)
    # ------------------------------------------------------------------
    
    # 1. Embeddings: Wrap your local class
    # Since we use legacy metrics, LangchainEmbeddingsWrapper works perfectly
    local_gemma = LocalGemmaEmbeddings() 
    judge_embeddings = LangchainEmbeddingsWrapper(local_gemma)

    # 2. LLM: Wrap your local Judge connection
    lc_llm = ChatOpenAI(
        base_url="http://localhost:8002/v1", 
        api_key="super-secret-judge-key",
        model="qwen2.5:14b",
        temperature=0,
        request_timeout=600,
        max_retries=3
    )
    judge_llm = LangchainLLMWrapper(lc_llm)

    metrics = [
        faithfulness, # Is the responses grounded in the retrieved context?
        answer_relevancy, # Is the response relevant to user question?
        answer_correctness, # Does the response match the reference answer?
        context_precision, # Are the contexts ranked by relevance?
        context_recall, # Are all relevant contexts successfully retrieved?
    ]

    print("🚀 Running Ragas Evaluation...")
    
    from ragas import evaluate
    
    my_run_config = RunConfig(
        timeout=600, 
        max_workers=1,
        max_retries=3
    )

    results = evaluate(
        dataset=dataset,
        metrics=metrics,
        llm=judge_llm, 
        embeddings=judge_embeddings,
        run_config=my_run_config
    )

    print("\n✅ Evaluation Complete!")
    print(results)

    # Save results
    df = results.to_pandas()

    # CLEANUP: Convert lists to strings for CSV
    cols_to_clean = ['response', 'reference', 'retrieved_contexts']
    for col in cols_to_clean:
        if col in df.columns:
            df[col] = df[col].apply(clean_text)

    # 2. AGGREGATION: Calculate averages
    # Select only numeric columns (the metrics)
    numeric_cols = df.select_dtypes(include=['number']).columns
    averages = df[numeric_cols].mean()

    print("\n📊 Aggregated Scores:")
    print(averages)

    # Create a summary row dictionary
    summary_row = {col: "" for col in df.columns}
    summary_row['user_input'] = "AGGREGATED METRICS (AVERAGE)" # Label the first column
    
    # Fill in the calculated averages
    for col, val in averages.items():
        summary_row[col] = val

    # Convert summary row to DataFrame and concatenate at the top
    summary_df = pd.DataFrame([summary_row])
    final_df = pd.concat([summary_df, df], ignore_index=True)

    output_file = os.path.join(current_dir, "ragas_evaluation_results.csv")
    final_df.to_csv(output_file, index=False)
    print(f"💾 Detailed results (with averages on line 1) saved to: {output_file}")

async def main():
    dataset_path = os.path.join(current_dir, "data", "wikieval_golden_dataset.json")
    rag_data = await generate_rag_responses(dataset_path)
    run_evaluation(rag_data)

if __name__ == "__main__":
    asyncio.run(main())
