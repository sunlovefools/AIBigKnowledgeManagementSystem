"""
Initialize the chat_messages collection in Astra DB for storing conversation history.

This script creates a simple collection without vector embeddings since chat messages
are stored as regular documents with conversationId, userEmail, role, text, and timestamp.

Usage:
    python scripts/initialise_chat_collection.py
"""

import os
from dotenv import load_dotenv
from astrapy import DataAPIClient

load_dotenv()

def create_chat_collection():
    """Create the chat_messages collection in Astra DB."""
    
    # Load credentials for chat collection, if fail then load main database credentials
    endpoint = os.environ.get("ASTRA_CHAT_DB_URL") or os.environ.get("ASTRA_DB_URL")
    token = os.environ.get("ASTRA_CHAT_DB_TOKEN") or os.environ.get("ASTRA_DB_TOKEN")
    keyspace = os.environ.get("ASTRA_CHAT_KEYSPACE") or os.environ.get("ASTRA_DB_KEYSPACE")
    collection_name = os.environ.get("ASTRA_CHAT_COLLECTION", "chat_messages")
    
    if not endpoint or not token:
        raise ValueError(
            "Missing required environment variables!\n"
        )
    
    # Show which database is being used
    if os.environ.get("ASTRA_CHAT_DB_URL"):
        print(f"Using chat-specific database: ASTRA_CHAT_DB_URL")
    else:
        print(f"Using main database: ASTRA_DB_URL")
    
    print(f"🔌 Connecting to Astra DB...")
    print(f"Keyspace: {keyspace}")
    client = DataAPIClient()
    database = client.get_database(endpoint, token=token, keyspace=keyspace)
    print(f"✅ Connected to database: {database.info().name}")
    
    # Check if collection already exists
    existing_collections = database.list_collection_names()
    if collection_name in existing_collections:
        print(f"Collection '{collection_name}' already exists.")
        collection = database.get_collection(collection_name)
        print(f"Using existing collection.")
        return collection
    
    # Create new collection (no vector field needed for chat history)
    print(f"🔨 Creating collection '{collection_name}'...")
    collection = database.create_collection(collection_name)
    print(f"✅ Collection '{collection_name}' created successfully!")
    
    return collection


if __name__ == "__main__":
    try:
        collection = create_chat_collection()
        print("\n✅ Chat collection initialization complete!")
    except Exception as e:
        print(f"\n❌ Failed to initialize chat collection: {e}")
        raise
