import sys
import os
import asyncio
import json
from typing import Dict, List, Any

import pandas as pd
from datasets import load_dataset
from huggingface_hub import HfApi
from dotenv import load_dotenv

load_dotenv()
# ------------------------------------------------------------------
# 🔌 PATH SETUP: Add 'backend' to sys.path to find 'app'
# ------------------------------------------------------------------
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

# Local imports
from app.service.rag.ingestion.chunker import split_parent_child_chunks
from app.service.rag.ingestion.chunk_polisher import polish_chunks
from app.vectordb.vectordb import upsert_documents


def _load_wikieval_data() -> Dict[str, List[Any]]:
    """Load WikiEval robustly across datasets metadata/cache incompatibilities."""
    try:
        dataset = load_dataset(
            "vibrantlabsai/WikiEval",
            split="train",
            verification_mode="no_checks",
            trust_remote_code=True,
        )
        return dataset.to_dict()
    except TypeError as error:
        if "dataclass type or instance" not in str(error):
            raise

        print("⚠️ Falling back to direct parquet load due to datasets metadata parsing error.")
        api = HfApi()
        files = api.list_repo_files("vibrantlabsai/WikiEval", repo_type="dataset")
        parquet_file = next((file for file in files if file.startswith("data/") and file.endswith(".parquet")), None)

        if parquet_file is None:
            raise RuntimeError("Could not find a parquet data file in vibrantlabsai/WikiEval") from error

        parquet_uri = f"hf://datasets/vibrantlabsai/WikiEval/{parquet_file}"
        dataframe = pd.read_parquet(parquet_uri)
        return dataframe.to_dict(orient="list")


async def main():
    print("📥 Downloading WikiEval dataset...")
    # 1. Load the raw dataset
    datasets = _load_wikieval_data()
    print(f"✅ Downloaded {len(datasets['question'])} items from WikiEval.")

    # ==============================================================================
    # STAGE 1: PREPARE DATA STRUCTURE (Golden Dataset Creation)
    # ==============================================================================
    print(f"📊 Formatting {len(datasets['question'])} items into the Golden Dataset structure...")
    
    golden_dataset = []
    
    answers = datasets["answer"]
    questions = datasets["question"]
    contexts = datasets["context_v2"]

    # Loop through the entire dataset to build the list first
    for i in range(len(answers)):
        raw_answer = answers[i]
        raw_question = questions[i]
        
        # Clean the strings (Remove "Answer: " / "Question: " prefixes)
        clean_answer = raw_answer.split("Answer: ")[1].strip() if "Answer: " in raw_answer else raw_answer.strip()
        clean_question = raw_question.split("Question: ")[1].strip() if "Question: " in raw_question else raw_question.strip()

        context_value = contexts[i]
        if hasattr(context_value, "tolist"):
            context_value = context_value.tolist()
        elif not isinstance(context_value, list):
            context_value = [context_value]

        normalized_context = [str(item).strip() for item in context_value if item is not None]

        # Create the dictionary structure you requested
        entry = {
            "answer": [clean_answer],       # Wrapped in list
            "question": [clean_question],   # Wrapped in list
            "context_v2": normalized_context,
            "row_number": f"row {i}"        # Identifier
        }
        
        golden_dataset.append(entry)

    data_dir = os.path.join(current_dir, "data")

    # Ensure the data directory exists
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    
    # Save this structured data to JSON so you have the golden dataset on disk
    output_path = os.path.join(data_dir, "wikieval_golden_dataset.json")
    with open(output_path, "w") as f:
        json.dump(golden_dataset, f, indent=4)
    print(f"✅ Golden Dataset JSON saved to: {output_path}")

    # ==============================================================================
    # STAGE 2: INGESTION (Process one by one)
    # ==============================================================================
    print("\n🚀 Starting Ingestion (Row by Row)...")

    for i, item in enumerate(golden_dataset):
        row_id = item['row_number']
        context_list = item['context_v2']
        
        # 1. Treat this specific row as a single file
        full_text = "\n".join(context_list)
        file_name = f"wikieval_{row_id.replace(' ', '_')}.txt" # e.g. "wikieval_row_0.txt"
        
        print(f"⚙️  Ingesting [{i+1}/{len(golden_dataset)}]: {file_name}")

        # 2. Split (Parent/Child)
        # We pass the text of THIS row only
        parent_chunks_models, child_chunks_models = split_parent_child_chunks(
            text=full_text, 
            file_name=file_name
        )

        # 3. Polish Child Chunks
        child_chunks_dicts = [chunk.model_dump(by_alias=False) for chunk in child_chunks_models]
        polished_child_chunks = polish_chunks(child_chunks_dicts)

        # 4. Prepare Parent Chunks
        parent_chunks_dicts = [chunk.model_dump(by_alias=True) for chunk in parent_chunks_models]
        
        # 5. Upsert to AstraDB
        try:
            await upsert_documents(
                parent_chunks=parent_chunks_dicts,
                child_chunks=polished_child_chunks
            )
        except Exception as e:
            print(f"❌ Failed to upsert {file_name}: {e}")
            # Optional: continue to next row even if one fails
            continue

    print("\n🎉 All processing complete!")

if __name__ == "__main__":
    asyncio.run(main())