"""
Initialize both chat-related collections in Astra DB.

Collections:
- chat_messages
- conversations

Usage:
    python scripts/initialise_chat_and_conversations_collections.py
"""

import os

from astrapy import DataAPIClient
from dotenv import load_dotenv

load_dotenv()


def get_astra_config() -> tuple[str, str, str]:
    """Load Astra endpoint, token, and keyspace from environment variables."""
    endpoint = os.environ.get("ASTRA_CHAT_DB_URL") or os.environ.get("ASTRA_DB_URL")
    token = os.environ.get("ASTRA_CHAT_DB_TOKEN") or os.environ.get("ASTRA_DB_TOKEN")
    keyspace = os.environ.get("ASTRA_CHAT_KEYSPACE") or os.environ.get("ASTRA_DB_KEYSPACE")

    if not endpoint or not token or not keyspace:
        raise ValueError(
            "Missing required environment variables!\n"
            "Required: ASTRA_CHAT_DB_URL/ASTRA_DB_URL, ASTRA_CHAT_DB_TOKEN/ASTRA_DB_TOKEN, "
            "ASTRA_CHAT_KEYSPACE/ASTRA_DB_KEYSPACE"
        )

    return endpoint, token, keyspace


def ensure_keyspace_exists(client: DataAPIClient, endpoint: str, token: str, keyspace: str) -> None:
    """Create the keyspace if it does not already exist."""
    control_db = client.get_database(endpoint, token=token)
    db_admin = control_db.get_database_admin()
    existing_keyspaces = db_admin.list_keyspaces()

    if keyspace in existing_keyspaces:
        print(f"INFO: Keyspace '{keyspace}' already exists.")
        return

    print(f"Creating keyspace '{keyspace}'...")
    db_admin.create_keyspace(keyspace)
    print(f"SUCCESS: Keyspace '{keyspace}' created successfully.")


def get_chat_database():
    """Get Astra database handle for chat keyspace, creating keyspace if required."""
    endpoint, token, keyspace = get_astra_config()

    if os.environ.get("ASTRA_CHAT_DB_URL"):
        print("Using chat-specific database: ASTRA_CHAT_DB_URL")
    else:
        print("Using main database: ASTRA_DB_URL")

    client = DataAPIClient()
    print("Ensuring keyspace exists in Astra DB...")
    print(f"Keyspace: {keyspace}")
    ensure_keyspace_exists(client, endpoint, token, keyspace)

    print("Connecting to Astra DB keyspace...")
    database = client.get_database(endpoint, token=token, keyspace=keyspace)
    print(f"SUCCESS: Connected to database: {database.info().name}")
    return database


def create_collection_if_missing(database, collection_name: str):
    """Create collection if it does not already exist."""
    existing_collections = database.list_collection_names()
    if collection_name in existing_collections:
        print(f"INFO: Collection '{collection_name}' already exists.")
        return database.get_collection(collection_name)

    print(f"Creating collection '{collection_name}'...")
    collection = database.create_collection(collection_name)
    print(f"SUCCESS: Collection '{collection_name}' created successfully!")
    return collection


def create_chat_collection(database=None):
    """Create the chat_messages collection."""
    collection_name = os.environ.get("ASTRA_CHAT_COLLECTION", "chat_messages")
    db = database or get_chat_database()
    return create_collection_if_missing(db, collection_name)


def create_conversations_collection(database=None):
    """Create the conversations collection."""
    collection_name = os.environ.get("ASTRA_CONVERSATIONS_COLLECTION", "conversations")
    db = database or get_chat_database()
    return create_collection_if_missing(db, collection_name)


def initialise_chat_and_conversations_collections():
    """Initialize both chat_messages and conversations collections."""
    database = get_chat_database()
    chat_collection = create_chat_collection(database=database)
    conversations_collection = create_conversations_collection(database=database)
    return {
        "chat_collection": chat_collection,
        "conversations_collection": conversations_collection,
    }


if __name__ == "__main__":
    try:
        initialise_chat_and_conversations_collections()
        print("\nSUCCESS: Chat and conversations collections initialization complete!")
    except Exception as e:
        print(f"\nERROR: Failed to initialize chat/conversations collections: {e}")
        raise
