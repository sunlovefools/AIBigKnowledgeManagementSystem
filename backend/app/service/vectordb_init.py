import os
from typing import Dict, Any
from langchain_astradb import AstraDBVectorStore, AstraDBStore
from .embedding_client import BeamGemmaEmbeddings

try:
    BEAM_EMBEDDINGS_INSTANCE = BeamGemmaEmbeddings()
except ValueError as error:
    # to the init_vector_db check, which will raise a final error if needed.
    print(f"Configuration Warning: {error}. Attempting database initialization.")

# Load Astra credentials from .env files
ASTRA_DB_URL = os.getenv("ASTRA_DB_URL")
ASTRA_DB_TOKEN = os.getenv("ASTRA_DB_TOKEN")

# Collection names
VECTOR_COLLECTION_NAME = "rag_child_vectors" # Child Chunks that have embeddings
PARENT_COLLECTION_NAME = "rag_parent_documents" # Parent Documents 


def init_vector_db():
    if not ASTRA_DB_URL or not ASTRA_DB_TOKEN:
        raise ValueError("ERROR:Missing Astra DB credentials in .env file")
        
    # Initialize a collections to hold the LangChain store objects
    collections: Dict[str, Any] = {}
    
    # 💡 1. Initialize Vector Store (Child Chunks) using AstraDBVectorStore
    print(f"Initializing vector store collection '{VECTOR_COLLECTION_NAME}' with LangChain...")
    
    try:
        # Instantiating the LangChain class ensures the collection exists with vector configuration.
        vector_store = AstraDBVectorStore(
            embedding=BEAM_EMBEDDINGS_INSTANCE, # Mocked only for setup/connection
            collection_name=VECTOR_COLLECTION_NAME,
            token=ASTRA_DB_TOKEN,
            api_endpoint=ASTRA_DB_URL,
        )
        collections['vector_store'] = vector_store
        print(f"✅ LangChain AstraDBVectorStore initialized for '{VECTOR_COLLECTION_NAME}'.")
    except Exception as e:
        print(f"❌ Failed to initialize AstraDBVectorStore: {e}")
        raise

    # 💡 2. Initialize Document Store (Parent Documents) using AstraDBStore
    print(f"Initializing document store collection '{PARENT_COLLECTION_NAME}' with LangChain...")
    
    try:
        # AstraDBStore is used for non-vector document persistence.
        parent_store = AstraDBStore(
            collection_name=PARENT_COLLECTION_NAME,
            token=ASTRA_DB_TOKEN,
            api_endpoint=ASTRA_DB_URL,
        )
        collections['parent_store'] = parent_store
        print(f"✅ LangChain AstraDBStore initialized for '{PARENT_COLLECTION_NAME}'.")
    except Exception as e:
        print(f"❌ Failed to initialize AstraDBStore: {e}")
        raise

    # Return the instantiated LangChain store objects.
    return collections