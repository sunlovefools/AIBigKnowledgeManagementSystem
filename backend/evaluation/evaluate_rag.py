import sys
import os
import json
import asyncio
import warnings # Import warnings to silence deprecation logs
import pandas as pd
from datasets import Dataset
from dotenv import load_dotenv
from typing import Any

# 1. FIX: Use Legacy Imports (Lowercase)
# These allow custom/local embeddings without strict "Modern" checks
from ragas.metrics import (
    faithfulness,      # Lowercase = Legacy (Compatible)
    answer_relevancy,
    answer_correctness,
    context_precision,
    context_recall,
)

from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.run_config import RunConfig

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

load_dotenv()

# ------------------------------------------------------------------
# 🔌 PATH SETUP
# ------------------------------------------------------------------
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

# Local imports
from app.vectordb.vectordb import search_and_retrieve_context
from app.service.rag.retrieval.answer_generator import generate_answer_api
from app.embedding.local_embedding_client import LocalGemmaEmbeddings 

# Filter warnings for cleaner output
warnings.filterwarnings("ignore", category=DeprecationWarning)


def _load_eval_judge_model_name() -> str:
    """
    Resolve Ollama judge model from environment.

    Priority:
    1) OLLAMA_EVAL_JUDGE_MODEL
    2) OLLAMA_MODEL (generic fallback)
    """
    model_name = (
        os.getenv("OLLAMA_EVAL_JUDGE_MODEL")
        or os.getenv("OLLAMA_MODEL")
        or ""
    ).strip()
    if not model_name:
        raise RuntimeError(
            "Missing Ollama judge model configuration. Set OLLAMA_EVAL_JUDGE_MODEL "
            "(or OLLAMA_MODEL) in your .env."
        )
    return model_name


class OllamaJudgeChatModel(BaseChatModel):
    """LangChain chat model wrapper backed by local Ollama Python client."""

    model: str
    temperature: float = 0.0
    request_timeout: float = 600.0

    @property
    def _llm_type(self) -> str:
        return "ollama_judge"

    @staticmethod
    def _to_ollama_messages(messages: list[BaseMessage]) -> list[dict[str, str]]:
        ollama_messages: list[dict[str, str]] = []
        for msg in messages:
            msg_type = getattr(msg, "type", "")
            if msg_type == "system":
                role = "system"
            elif msg_type == "ai":
                role = "assistant"
            else:
                role = "user"

            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            ollama_messages.append({"role": role, "content": content})
        return ollama_messages

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            from ollama import Client
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Missing required dependency 'ollama'. "
                "Install it to run RAG evaluation with local Ollama judge."
            ) from exc

        options: dict[str, Any] = {"temperature": self.temperature}
        if stop:
            options["stop"] = stop

        client = Client(timeout=self.request_timeout)
        try:
            response = client.chat(
                model=self.model,
                messages=self._to_ollama_messages(messages),
                options=options,
            )
        except Exception as exc:
            raise RuntimeError(f"Ollama judge chat failed: {exc}") from exc

        message = response.get("message") if isinstance(response, dict) else None
        content = ""
        if isinstance(message, dict):
            raw_content = message.get("content")
            content = raw_content if isinstance(raw_content, str) else str(raw_content)

        generation = ChatGeneration(message=AIMessage(content=content))
        return ChatResult(generations=[generation])


def _extract_page_contents(rag_docs: list[dict[str, Any]]) -> list[str]:
    """Convert retrieved parent doc dicts into plain context strings for RAGAS contexts."""
    return [str(doc.get("page_content", "")) for doc in rag_docs if isinstance(doc, dict)]

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
            rag_docs = await search_and_retrieve_context(query=question, top_k=5)
            print(rag_docs)
            answer = await generate_answer_api(rag_docs, question)

            questions.append(question)
            ground_truths.append(ground_truth) 
            generated_answers.append(answer)
            retrieved_contexts.append(_extract_page_contents(rag_docs))

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

    # 2. LLM: Wrap local Ollama judge via Python client (no manual HTTP endpoint calls)
    judge_model_name = _load_eval_judge_model_name()
    lc_llm = OllamaJudgeChatModel(
        model=judge_model_name,
        temperature=0.0,
        request_timeout=600.0,
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
