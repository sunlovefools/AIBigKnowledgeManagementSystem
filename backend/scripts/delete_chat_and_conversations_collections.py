"""
Delete both chat-related collections in Astra DB without deleting keyspace.

Collections:
- chat_messages
- conversations

Usage:
    python scripts/delete_chat_and_conversations_collections.py
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


def get_database_if_keyspace_exists(client: DataAPIClient, endpoint: str, token: str, keyspace: str):
    """Return keyspace database handle if keyspace exists; otherwise return None."""
    control_db = client.get_database(endpoint, token=token)
    db_admin = control_db.get_database_admin()
    existing_keyspaces = db_admin.list_keyspaces()

    if keyspace not in existing_keyspaces:
        print(f"INFO: Keyspace '{keyspace}' does not exist. Nothing to delete.")
        return None

    return client.get_database(endpoint, token=token, keyspace=keyspace)


def delete_chat_and_conversations_collections():
    """Delete chat_messages and conversations collections from the configured keyspace."""
    endpoint, token, keyspace = get_astra_config()
    chat_collection_name = os.environ.get("ASTRA_CHAT_COLLECTION", "chat_messages")
    conversations_collection_name = os.environ.get("ASTRA_CONVERSATIONS_COLLECTION", "conversations")

    if os.environ.get("ASTRA_CHAT_DB_URL"):
        print("Using chat-specific database: ASTRA_CHAT_DB_URL")
    else:
        print("Using main database: ASTRA_DB_URL")

    client = DataAPIClient()
    print(f"Checking keyspace: {keyspace}")
    database = get_database_if_keyspace_exists(client, endpoint, token, keyspace)
    if database is None:
        return

    existing_collections = set(database.list_collection_names())
    target_collections = [chat_collection_name, conversations_collection_name]

    for collection_name in target_collections:
        if collection_name in existing_collections:
            print(f"Deleting collection '{collection_name}'...")
            database.drop_collection(collection_name)
            print(f"SUCCESS: Collection '{collection_name}' deleted.")
        else:
            print(f"INFO: Collection '{collection_name}' does not exist. Skipping.")

    print("SUCCESS: Collection cleanup complete. Keyspace was not deleted.")


if __name__ == "__main__":
    try:
        delete_chat_and_conversations_collections()
    except Exception as e:
        print(f"\nERROR: Failed to delete chat/conversations collections: {e}")
        raise
