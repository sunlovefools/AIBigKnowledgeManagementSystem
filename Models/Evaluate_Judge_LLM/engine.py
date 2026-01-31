import logging
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global storage
llm_client = None
# You requested Qwen 14B
MODEL_ID = "qwen2.5:14b" 

def load_model():
    """
    Initializes the connection to Ollama.
    """
    global llm_client
    print(f"🚀 [Judge Service] Connecting to Ollama model: {MODEL_ID}...")
    
    try:
        llm_client = ChatOllama(
            model=MODEL_ID,
            temperature=0,      # Judge needs to be deterministic
            num_ctx=8192,       # Large context for RAG evaluation
            keep_alive="5m"
        )
        # Test invocation
        # llm_client.invoke("test")
        print("✅ [Judge Service] Connected to Ollama successfully!")
    except Exception as e:
        print(f"❌ [Judge Service] Failed to connect: {e}")
        raise e

async def generate_judgment(messages: list[dict]) -> str:
    """
    Processes a list of chat messages (Role/Content) and returns the text response.
    Used by Ragas to send "System" instructions and "User" grading tasks.
    """
    if not llm_client:
        raise RuntimeError("Model not loaded. Call load_model() first.")

    logger.info("⚖️ Generating judgment...")
    print(messages)
    print("\n")
    # Convert dict messages to LangChain Message objects
    langchain_messages = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        
        if role == "system":
            langchain_messages.append(SystemMessage(content=content))
        elif role == "user":
            langchain_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            langchain_messages.append(AIMessage(content=content))

    logger.info(f"⚖️ Judging request with {len(langchain_messages)} messages.")

    # Invoke the model
    response = await llm_client.ainvoke(langchain_messages)
    
    print(response.content)
    print("\n")

    return response.content