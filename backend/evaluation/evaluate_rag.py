import sys
import os
import json
import asyncio
import re
import warnings # Import warnings to silence deprecation logs
import pandas as pd
from datasets import Dataset
from dotenv import load_dotenv
from typing import Any

import mlflow

# To view MLflow results after running evaluation:
# cd backend
# .venv/Scripts/python -m mlflow ui
# Then open http://127.0.0.1:5000


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

METRIC_REGISTRY = {
    "faithfulness": faithfulness,
    "answer_relevancy": answer_relevancy,
    "answer_correctness": answer_correctness,
    "context_precision": context_precision,
    "context_recall": context_recall,
}

# Toggle metrics here using True/False.
METRIC_ENABLED = {
    "faithfulness": True,
    "answer_relevancy": True,
    "answer_correctness": True,
    "context_precision": True,
    "context_recall": True,
}


def _extract_balanced_json_block(text: str) -> str | None:
    """
    Return first balanced JSON object/array found in text.

    This helps recover valid JSON from model outputs that include prose,
    markdown fences, or trailing notes.
    """
    if not isinstance(text, str):
        return None

    start_idx = None
    opening = ""
    closing = ""
    for idx, ch in enumerate(text):
        if ch == "{":
            start_idx = idx
            opening = "{"
            closing = "}"
            break
        if ch == "[":
            start_idx = idx
            opening = "["
            closing = "]"
            break

    if start_idx is None:
        return None

    depth = 0
    in_string = False
    escaped = False

    for idx in range(start_idx, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "\"":
                in_string = False
            continue

        if ch == "\"":
            in_string = True
            continue

        if ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return text[start_idx : idx + 1]

    return None


def _sanitize_judge_output(content: str) -> str:
    """
    Remove common reasoning/formatting wrappers so RAGAS output parsers can parse reliably.
    """
    if not isinstance(content, str):
        return str(content)

    cleaned = content.strip()
    if not cleaned:
        return cleaned

    # Drop "thinking" blocks emitted by some reasoning models.
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()

    # Prefer explicit JSON code-fence content when present.
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if fence_match:
        fenced = fence_match.group(1).strip()
        if fenced:
            cleaned = fenced

    # Keep only first JSON block if extra text is still present.
    json_block = _extract_balanced_json_block(cleaned)
    if json_block:
        return json_block.strip()

    return cleaned


def _print_judge_request(
    model_name: str,
    messages: list[BaseMessage],
    options: dict[str, Any],
) -> None:
    """Print every judge-model request payload for debugging."""
    printable_messages: list[dict[str, str]] = []
    for message in messages:
        message_type = getattr(message, "type", "")
        if message_type == "system":
            role = "system"
        elif message_type == "ai":
            role = "assistant"
        else:
            role = "user"

        content = message.content if isinstance(message.content, str) else str(message.content)
        printable_messages.append({"role": role, "content": content})

    payload = {
        "model": model_name,
        "messages": printable_messages,
        "format": "json",
        "options": options,
    }
    print("\n===== OLLAMA JUDGE REQUEST =====")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("===== END OLLAMA JUDGE REQUEST =====\n")


def _coerce_object_to_dict(value: Any) -> dict[str, Any] | None:
    """Best-effort conversion for SDK objects (e.g., pydantic responses)."""
    if isinstance(value, dict):
        return value

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass

    as_dict = getattr(value, "dict", None)
    if callable(as_dict):
        try:
            dumped = as_dict()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass

    return None


def _normalize_content_to_text(content: Any) -> str:
    """Serialize model content safely to text expected by LangChain/RAGAS."""
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def _extract_ollama_response_content(response: Any) -> str:
    """Extract assistant content from Ollama chat response across object/dict variants."""
    message_obj: Any = None

    response_dict = _coerce_object_to_dict(response)
    if response_dict is not None:
        message_obj = response_dict.get("message")
    elif hasattr(response, "message"):
        message_obj = getattr(response, "message")

    if message_obj is None:
        return ""

    if isinstance(message_obj, dict):
        return _normalize_content_to_text(message_obj.get("content"))

    message_dict = _coerce_object_to_dict(message_obj)
    if message_dict is not None:
        return _normalize_content_to_text(message_dict.get("content"))

    if hasattr(message_obj, "content"):
        return _normalize_content_to_text(getattr(message_obj, "content"))

    return _normalize_content_to_text(message_obj)


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
        _print_judge_request(
            model_name=self.model,
            messages=messages,
            options=options,
        )
        try:
            response = client.chat(
                model=self.model,
                messages=self._to_ollama_messages(messages),
                format="json",
                options=options,
            )
        except Exception as exc:
            raise RuntimeError(f"Ollama judge chat failed: {exc}") from exc

        content = _extract_ollama_response_content(response)
        if not content.strip():
            response_dump = _coerce_object_to_dict(response)
            if response_dump is not None:
                print("OLLAMA JUDGE RAW RESPONSE:")
                print(json.dumps(response_dump, ensure_ascii=False, indent=2))
            else:
                print(f"OLLAMA JUDGE RAW RESPONSE (repr): {repr(response)}")
        content = _sanitize_judge_output(content)

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


def _strip_source_citations(text: str) -> str:
    """Remove source citation snippets like '(source: ...)' from model outputs."""
    if not isinstance(text, str):
        return str(text)
    cleaned = re.sub(
        r"\s*\((?:source|sources)\s*:[^)]*\)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def _resolve_enabled_metrics() -> tuple[list[str], list[Any]]:
    """Resolve metric objects based on top-level METRIC_ENABLED flags."""
    selected_names = [
        metric_name
        for metric_name in METRIC_REGISTRY.keys()
        if METRIC_ENABLED.get(metric_name, False)
    ]
    if not selected_names:
        available = ", ".join(METRIC_REGISTRY.keys())
        raise RuntimeError(
            f"No metrics enabled. Set at least one metric to True in METRIC_ENABLED. "
            f"Available metrics: {available}"
        )

    selected_metrics = [METRIC_REGISTRY[name] for name in selected_names]
    return selected_names, selected_metrics


def _print_metric_values(df: pd.DataFrame, metric_names: list[str]) -> None:
    """Print one averaged score per selected metric."""
    print("\nMetric values:")
    for metric_name in metric_names:
        column_name = metric_name
        if column_name not in df.columns and metric_name == "answer_relevancy":
            if "answer_relevance" in df.columns:
                column_name = "answer_relevance"

        if column_name not in df.columns:
            print(f"- {metric_name}: unavailable (column missing in results)")
            continue

        numeric_series = pd.to_numeric(df[column_name], errors="coerce").dropna()
        if numeric_series.empty:
            print(f"- {metric_name}: unavailable (no numeric score returned)")
            continue

        print(f"- {metric_name}: {numeric_series.mean():.4f}")


async def generate_rag_responses(dataset_path: str, top_k: int = 7):
    print(f"📂 Loading Golden Dataset from: {dataset_path}")

    with open(dataset_path, "r") as f:
        golden_data = json.load(f)

    golden_data = golden_data[:120]
    
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
            rag_docs = await search_and_retrieve_context(query=question, top_k=top_k)
            answer = await generate_answer_api(rag_docs, question)
            answer = _strip_source_citations(answer)

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

def run_evaluation(data_dict, top_k: int = 7):
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

    metric_names, metrics = _resolve_enabled_metrics()
    print(f"Selected metrics: {', '.join(metric_names)}")

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

    import time
    time.sleep(1)
    sys.stdout.flush()

    print("\n✅ Evaluation Complete!")
    print(results)

    # Save results
    df = results.to_pandas()
    _print_metric_values(df, metric_names)

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
    if "question" in summary_row:
        summary_row["question"] = "AGGREGATED METRICS (AVERAGE)"
    elif summary_row:
        first_col = next(iter(summary_row))
        summary_row[first_col] = "AGGREGATED METRICS (AVERAGE)"
    
    # Fill in the calculated averages
    for col, val in averages.items():
        summary_row[col] = val

    # Convert summary row to DataFrame and concatenate at the top
    summary_df = pd.DataFrame([summary_row])
    final_df = pd.concat([summary_df, df], ignore_index=True)

    output_file = os.path.join(current_dir, "ragas_evaluation_results.csv")
    final_df.to_csv(output_file, index=False)
    print(f"💾 Detailed results (with averages on line 1) saved to: {output_file}")

    # ------------------------------------------------------------------
    # 📊 MLflow: Log experiment results
    # ------------------------------------------------------------------
    try:
        judge_model = _load_eval_judge_model_name()
        mlflow.set_experiment("RAG Evaluation")
        with mlflow.start_run():
            # Parameters — what settings were used
            mlflow.log_param("top_k", top_k)
            mlflow.log_param("num_questions", len(data_dict.get("question", [])))
            mlflow.log_param("judge_model", judge_model)
            mlflow.log_param("enabled_metrics", ", ".join(metric_names))

            # Metrics — RAGAS scores
            for col in numeric_cols:
                score = float(averages[col])
                mlflow.log_metric(col, score)

            # Artifact — full CSV with per-question results
            mlflow.log_artifact(output_file)

            print("📊 MLflow: experiment results logged successfully.")
            print(f"   Run UI: run `mlflow ui` in terminal, then open http://localhost:5000")
    except Exception as mlflow_err:
        print(f"⚠️  MLflow logging failed (non-critical): {mlflow_err}")

async def main():
    top_k = 7  # Change this value to experiment with different retrieval depths
    dataset_path = os.path.join(current_dir, "data", "golden_dataset.json")
    rag_data = await generate_rag_responses(dataset_path, top_k=top_k)
    run_evaluation(rag_data, top_k=top_k)

if __name__ == "__main__":
    asyncio.run(main())
