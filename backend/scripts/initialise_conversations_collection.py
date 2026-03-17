"""
Initialize the conversations collection in Astra DB for chat metadata.

Usage:
    python scripts/initialise_conversations_collection.py
"""

import os

from astrapy import DataAPIClient
from dotenv import load_dotenv

load_dotenv()


def create_conversations_collection():
    """Create the conversations collection in Astra DB."""

    endpoint = os.environ.get("ASTRA_CHAT_DB_URL") or os.environ.get("ASTRA_DB_URL")
    token = os.environ.get("ASTRA_CHAT_DB_TOKEN") or os.environ.get("ASTRA_DB_TOKEN")
    keyspace = os.environ.get("ASTRA_CHAT_KEYSPACE") or os.environ.get("ASTRA_DB_KEYSPACE")
    collection_name = os.environ.get("ASTRA_CONVERSATIONS_COLLECTION", "conversations")

    if not endpoint or not token:
        raise ValueError(
            "Missing required environment variables!\n"
        )

    print("🔌 Connecting to Astra DB...")
    print(f"   Keyspace: {keyspace}")
    client = DataAPIClient()
    database = client.get_database(endpoint, token=token, keyspace=keyspace)
    print(f"✅ Connected to database: {database.info().name}")

    existing_collections = database.list_collection_names()
    if collection_name in existing_collections:
        print(f"ℹ️  Collection '{collection_name}' already exists.")
        return database.get_collection(collection_name)

    print(f"🔨 Creating collection '{collection_name}'...")
    collection = database.create_collection(collection_name)
    print(f"✅ Collection '{collection_name}' created successfully!")
    return collection


if __name__ == "__main__":
    try:
        create_conversations_collection()
        print("\n✅ Conversations collection initialization complete!")
    except Exception as e:
        print(f"\n❌ Failed to initialize conversations collection: {e}")
        raise
