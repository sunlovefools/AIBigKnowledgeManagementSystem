"""
Initialize the user_chat_preferences collection in Astra DB.

Usage:
    python scripts/initialise_user_chat_preferences_collection.py
"""

import os

from astrapy import DataAPIClient
from dotenv import load_dotenv

load_dotenv()


def create_user_chat_preferences_collection():
    """Create user_chat_preferences collection in Astra DB."""

    endpoint = os.environ.get("ASTRA_CHAT_DB_URL") or os.environ.get("ASTRA_DB_URL")
    token = os.environ.get("ASTRA_CHAT_DB_TOKEN") or os.environ.get("ASTRA_DB_TOKEN")
    keyspace = os.environ.get("ASTRA_CHAT_KEYSPACE") or os.environ.get("ASTRA_DB_KEYSPACE")
    collection_name = os.environ.get("ASTRA_USER_CHAT_PREFERENCES_COLLECTION", "user_chat_preferences")

    if not endpoint or not token:
        raise ValueError(
            "Missing required environment variables!\n"
            "Please set in backend/.env:\n"
            "  ASTRA_CHAT_DB_URL (or ASTRA_DB_URL)\n"
            "  ASTRA_CHAT_DB_TOKEN (or ASTRA_DB_TOKEN)\n"
            "  ASTRA_CHAT_KEYSPACE (or ASTRA_DB_KEYSPACE)"
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
    print("📑 Collection is ready for user-level chat settings.")
    print("\nCollection document shape:")
    print("  - userEmail: string")
    print("  - defaultConversationId: string | null")
    print("  - recentConversationIds: string[]")
    print("  - theme: string")
    print("  - notificationsEnabled: bool")
    print("  - updatedAt: string (ISO timestamp)")

    return collection


if __name__ == "__main__":
    try:
        create_user_chat_preferences_collection()
        print("\n✅ User chat preferences collection initialization complete!")
    except Exception as e:
        print(f"\n❌ Failed to initialize user chat preferences collection: {e}")
        raise
