import logging
import json
import re
from typing import Union, List
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("rag_service.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Global storage
llm_client = None
MODEL_ID = "qwen2.5:14b"


def load_model():
    global llm_client
    print(f"🚀 [Answer Service] Connecting to Ollama model: {MODEL_ID}...")

    try:
        llm_client = ChatOllama(
            model=MODEL_ID,
            temperature=0,
            num_ctx=8192,
            top_k=40,
            keep_alive="5m"
        )
        print("✅ [Answer Service] Connected to Ollama successfully!")
    except Exception as e:
        print(f"❌ Failed to connect to Ollama: {e}")
        raise e


def generate_answer(rag_context: Union[str, List], user_query: str) -> str:
    """
    Citation-enforced RAG.
    Supports:
    - rag_context as string (old format)
    - structured retrieved_contexts (list)
    """

    if not llm_client:
        raise RuntimeError("Ollama client is not initialized.")

    # -------------------------
    # Handle Structured Context
    # -------------------------
    if isinstance(rag_context, list):
        structured_context = {
            "retrieved_contexts": [
                {
                    "filename": chunk.filename,
                    "page": getattr(chunk, "page", None),
                    "chunk_context": chunk.chunk_context
                }
                for chunk in rag_context
            ]
        }

        context_text = json.dumps(structured_context, indent=2)
        valid_filenames = [chunk.filename for chunk in rag_context]

    else:
        # Old format fallback (no strict filename validation)
        context_text = rag_context
        valid_filenames = None  # 🔥 disable strict filename matching

    # -------------------------
    # System Prompt
    # -------------------------
    system_prompt = """
You are an intelligent Answer Generation Assistant for a Retrieval-Augmented Generation (RAG) system.

STRICT RULES:

1. Use ONLY the provided <CONTEXT>.
2. You MUST include a citation at the end of your answer.
3. Citation format: (filename)
4. If answer not found, respond EXACTLY:
   No answer found in the provided context.
5. Do NOT use external knowledge.
6. Do NOT explain reasoning.
7. Do NOT add extra commentary.
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", """
<CONTEXT>
{context_text}
</CONTEXT>

<QUERY>
{user_query}
</QUERY>

Provide the final answer below:
""")
    ])

    chain = prompt | llm_client

    logger.info("--------------- START ANSWER GENERATION ---------------")
    logger.info("USER QUERY: %s", user_query)
    logger.info("RAG CONTEXT: %s", context_text)

    try:
        response = chain.invoke({
            "context_text": context_text,
            "user_query": user_query
        })

        answer = response.content.strip()

        if "No answer found" in answer:
            return "No answer found in the provided context."

        # -------------------------
        # Citation Validation
        # -------------------------

        # Require that some citation exists
        if not re.search(r"\([^)]+\)", answer):
            logger.warning("❌ Citation missing.")
            return "No answer found in the provided context."

        # Only enforce strict matching if we have real filenames
        if valid_filenames:
            if not any(f"({name})" in answer for name in valid_filenames):
                logger.warning("❌ Citation does not match valid filenames.")
                return "No answer found in the provided context."

        return answer

    except Exception as e:
        logger.error("❌ Inference Error: %s", str(e))
        return "Error generating answer."
