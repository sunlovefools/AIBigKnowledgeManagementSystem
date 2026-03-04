import sys
import os
import json
import asyncio
from typing import Any, Dict
from dotenv import load_dotenv
load_dotenv()

# --- PATH SETUP ---
# Add 'backend' to python path so we can import 'app'
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

# --- IMPORTS ---
from app.vectordb.vectordb import search_and_retrieve_context
from retrieval_metrics import calculate_hit_rate, calculate_mrr


def _extract_page_contents(retrieved_docs: list[dict[str, Any]]) -> list[str]:
    """Convert retrieved parent document dicts into plain content strings for metrics."""
    return [str(doc.get("page_content", "")) for doc in retrieved_docs if isinstance(doc, dict)]


async def run_retrieval_test(dataset_path: str, top_k: int = 5):
    print(f"📂 Loading Dataset from: {dataset_path}")
    
    with open(dataset_path, "r") as f:
        data: list[Dict] = json.load(f)

    # We will accumulate scores incrementally
    total_hits = 0.0
    total_mrr = 0.0
    
    # These lists are kept if you want to inspect full results later, 
    # but we won't use them for the batch calculation anymore.
    retrieved_results = []
    golden_contexts_list = []

    print(f"🚀 Starting Retrieval Evaluation for {len(data)} items (Top-K={top_k})...\n")

    for index, item in enumerate(data):
        # 1. Get Question
        question_val = item["question"]
        question = question_val[0]

        # 2. Get Golden Context (context_v2)
        # Handle cases where it might be a string or a list
        context_val = item.get("context_v2", [])
        
        # if isinstance(context_val, str):
        #     context_val = [context_val]
        
        golden_contexts_list.append(context_val)

        print(f"🔹 [{index+1}/{len(data)}] Query: {question[:60]}...")

        # 3. Run Retrieval
        try:
            # This calls your Vector DB logic
            retrieved_docs = await search_and_retrieve_context(query=question, top_k=top_k)
        except Exception as e:
            print(f"   ❌ Error retrieving: {e}")
            retrieved_docs = []

        retrieved_results.append(retrieved_docs)

        retrieved_contents = _extract_page_contents(retrieved_docs)
        current_hit = calculate_hit_rate(retrieved_contents, context_val)
        current_mrr = calculate_mrr(retrieved_contents, context_val)
        
        total_hits += current_hit
        total_mrr += current_mrr

    # --- CALCULATE METRICS ---
    print("\n" + "="*40)
    print("📊 RETRIEVAL PERFORMANCE REPORT")
    print("="*40)

    # Calculate averages from the accumulated totals
    num_queries = len(data)
    final_hit_rate = total_hits / num_queries if num_queries > 0 else 0
    final_mrr = total_mrr / num_queries if num_queries > 0 else 0

    print(f"✅ Total Queries: {num_queries}")
    print("-" * 40)
    print(f"🎯 Hit Rate: {final_hit_rate:.4f}")
    print(f"🥇 MRR: {final_mrr:.4f}")
    print("="*40)

if __name__ == "__main__":
    # Point to your existing golden dataset
    dataset_file = os.path.join(current_dir, "data", "wikieval_golden_dataset.json")
    
    # Run the async main loop
    asyncio.run(run_retrieval_test(dataset_file, top_k=2))
