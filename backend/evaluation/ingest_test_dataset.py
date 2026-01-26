import sys
import os
import asyncio
import json
from datasets import load_dataset
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


async def main():
    print("📥 Downloading WikiEval dataset...")
    # 1. Load the raw dataset
    datasets = load_dataset("vibrantlabsai/WikiEval", split="train") 

    # ==============================================================================
    # STAGE 1: PREPARE DATA STRUCTURE (Golden Dataset Creation)
    # ==============================================================================
    print(f"📊 Formatting {len(datasets)} items into the Golden Dataset structure...")
    
    golden_dataset = []
    
    answers = datasets["answer"]
    questions = datasets["question"]
    contexts = datasets["context_v2"]

    # Loop through the entire dataset to build the list first
    for i in range(len(datasets)):
        raw_answer = answers[i]
        raw_question = questions[i]
        
        # Clean the strings (Remove "Answer: " / "Question: " prefixes)
        clean_answer = raw_answer.split("Answer: ")[1].strip() if "Answer: " in raw_answer else raw_answer.strip()
        clean_question = raw_question.split("Question: ")[1].strip() if "Question: " in raw_question else raw_question.strip()

        # Create the dictionary structure you requested
        entry = {
            "answer": [clean_answer],       # Wrapped in list
            "question": [clean_question],   # Wrapped in list
            "context_v2": contexts[i],      # Already a list
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