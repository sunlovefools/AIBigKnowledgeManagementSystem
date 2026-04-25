#!/usr/bin/env python3
"""
Clear stored chat history without touching vectors, files, or user collections.

Collections cleared from the chat/metadata keyspace:
- chat_messages
- conversations
"""

from __future__ import annotations

import os

from astrapy import DataAPIClient
from dotenv import load_dotenv

load_dotenv()


def _optional(name: str) -> str | None:
    value = str(os.getenv(name) or "").strip()
    return value or None


def _require(name: str) -> str:
    value = _optional(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _get_chat_database():
    endpoint = _optional("ASTRA_CHAT_DB_URL") or _require("ASTRA_DB_URL")
    token = _optional("ASTRA_CHAT_DB_TOKEN") or _require("ASTRA_DB_TOKEN")
    keyspace = _optional("ASTRA_CHAT_KEYSPACE") or _optional("ASTRA_DB_KEYSPACE")
    client = DataAPIClient()
    if keyspace:
        return client.get_database(endpoint, token=token, keyspace=keyspace)
    return client.get_database(endpoint, token=token)


def _clear_collection(database, collection_name: str) -> None:
    existing = set(database.list_collection_names())
    if collection_name not in existing:
        print(f"Collection not found, skipping: {collection_name}")
        return

    collection = database.get_collection(collection_name)
    result = collection.delete_many({})
    deleted_count = getattr(result, "deleted_count", None)
    suffix = f" ({deleted_count} rows)" if deleted_count is not None else ""
    print(f"Cleared {collection_name}{suffix}")


def clear_chat_history() -> None:
    chat_messages_name = _optional("ASTRA_CHAT_COLLECTION") or "chat_messages"
    conversations_name = _optional("ASTRA_CONVERSATIONS_COLLECTION") or "conversations"

    database = _get_chat_database()
    for collection_name in [chat_messages_name, conversations_name]:
        _clear_collection(database, collection_name)

    print("\nChat history cleared. Vector and collection metadata were not touched.")


if __name__ == "__main__":
    confirm = input(
        "This will delete chat_messages and conversations only. Continue? (y/n): "
    ).strip().lower()
    if confirm == "y":
        clear_chat_history()
    else:
        print("Operation cancelled.")
