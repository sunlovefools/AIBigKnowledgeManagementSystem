import logging
import os
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("rag_service.log", encoding="utf-8"),  # Writes logs to this file
        logging.StreamHandler()                  # Writes logs to the terminal
    ]
)
logger = logging.getLogger(__name__)

# Global storage
llm_client = None
MODEL_ID = "qwen2.5:14b"  # Ensure you ran: ollama pull qwen2.5:14b
def load_model():
    """
    Initializes the connection to Ollama.
    """
    global llm_client
    print(f"🚀 [Answer Service] Connecting to Ollama model: {MODEL_ID}...")
    
    try:
        # We initialize the client. Note: Ollama must be running (ollama serve)
        llm_client = ChatOllama(
            model=MODEL_ID,
            temperature=0,
            num_ctx = 8192,
            top_k = 40,
            keep_alive="5m"
        )
        # Optional: dry run to ensure connection
        # llm_client.invoke("test")
        print("✅ [Answer Service] Connected to Ollama successfully!")
    except Exception as e:
        print(f"❌ Failed to connect to Ollama: {e}")
        raise e

def generate_answer(rag_context: str, user_query: str) -> str:
    """
    Constructs the RAG prompt and invokes Ollama.
    """
    if not llm_client:
        raise RuntimeError("Ollama client is not initialized.")

    # 1. Define the System Prompt
    system_prompt = f"""You are an intelligent, expert-level Answer Generation Assistant for a Retrieval-Augmented Generation (RAG) system. Your sole purpose is to synthesize a response based strictly on the provided context.

### Instructions
1.  **STRICT GROUNDING & REASONING:**
    * Your answer MUST be derived **ONLY** from the text provided in the <CONTEXT> tags. **NEVER** use external knowledge, speculate, or invent facts.
    * **Internal Verification:** Before writing, verify that the synthesized answer is fully supported by the <CONTEXT>. Do not show this verification step.
    * **Source Text Adherence:** Where possible, directly use or closely paraphrase the **exact phrasing** from the source text to construct your answer to maintain high fidelity.

2.  **UNANSWERABLE CONDITION:**
    * If the <CONTEXT> does not contain sufficient information to fully answer the user's <QUERY>, you **MUST** respond with the **EXACT** phrase: `No answer found in the provided context.` Do not add any other text or formatting.

3. **ANSWER-ONLY OUTPUT CONSTRAINT:**
   * Output **ONLY** the final answer that directly responds to the user's <QUERY>.
   * Do **NOT** include any additional commentary beyond what is strictly required to answer the question.
   
4.  **FORMAT:**
    * Produce a clear, highly structured, and easy-to-read answer. Use appropriate markdown (headings, bolding, bullet points) for readability.

### Context for Grounding
<CONTEXT>
{rag_context}
</CONTEXT>
"""

    # 2. Define the Prompt Template
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Based ONLY on the context provided, answer the following user query:\n<QUERY>\n{user_query}\n</QUERY>\n\nProduce the final, structured answer here:\n<FINAL_ANSWER>")
    ])

    # 3. Create Chain
    chain = prompt | llm_client

    logger.info("--------------- START ANSWER GENERATION ---------------")
    logger.info("USER QUERY: %s", user_query)

    logger.info("--------------- RAG CONTEXT ---------------")
    logger.info("RAG CONTEXT: %s", rag_context)

    # 4. Invoke
    try:
        response = chain.invoke({
            "rag_context": rag_context, 
            "user_query": user_query
        })
        
        # Cleanup: Ollama usually handles the stop tokens well, but we ensure cleanliness
        answer = response.content.replace("<FINAL_ANSWER>", "").strip()
        
        if "No answer found" in answer:
            return "No answer found in the provided context."
            
        return answer

    except Exception as e:
        print(f"❌ Inference Error: {e}")
        return "Error generating answer."