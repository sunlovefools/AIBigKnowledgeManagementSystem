#!/usr/bin/env python3
"""
Delete Astra DB collections used by this project.

Drops vector collections from vector keyspace:
- Default_Child_Collection
- Default_Parent_Collection

Drops metadata collections from chat keyspace:
- chat_messages
- conversations
- user_collections
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


def _get_vector_database():
    endpoint = _require("ASTRA_DB_URL")
    token = _require("ASTRA_DB_TOKEN")
    keyspace = _optional("ASTRA_DB_KEYSPACE")
    client = DataAPIClient()
    if keyspace:
        return client.get_database(endpoint, token=token, keyspace=keyspace)
    return client.get_database(endpoint, token=token)


def _get_chat_database():
    endpoint = _optional("ASTRA_CHAT_DB_URL") or _require("ASTRA_DB_URL")
    token = _optional("ASTRA_CHAT_DB_TOKEN") or _require("ASTRA_DB_TOKEN")
    keyspace = _optional("ASTRA_CHAT_KEYSPACE") or _optional("ASTRA_DB_KEYSPACE")
    client = DataAPIClient()
    if keyspace:
        return client.get_database(endpoint, token=token, keyspace=keyspace)
    return client.get_database(endpoint, token=token)


def _drop_if_exists(database, collection_name: str):
    existing = set(database.list_collection_names())
    if collection_name not in existing:
        print(f"Collection not found, skipping: {collection_name}")
        return
    print(f"Dropping collection: {collection_name}")
    database.drop_collection(collection_name)
    print(f"Dropped: {collection_name}")


def delete_all_collections():
    child_collection_name = (
        _optional("ASTRA_CHILD_COLLECTION")
        or _optional("CHILD_COLLECTION_NAME")
        or "Default_Child_Collection"
    )
    parent_collection_name = (
        _optional("ASTRA_PARENT_COLLECTION")
        or _optional("PARENT_COLLECTION_NAME")
        or "Default_Parent_Collection"
    )
    chat_collection_name = _optional("ASTRA_CHAT_COLLECTION") or "chat_messages"
    conversations_collection_name = _optional("ASTRA_CONVERSATIONS_COLLECTION") or "conversations"
    user_collections_name = _optional("ASTRA_USER_COLLECTIONS_COLLECTION") or "user_collections"

    print("Connecting to vector database...")
    vector_database = _get_vector_database()
    print("Connecting to chat/metadata database...")
    chat_database = _get_chat_database()

    for collection_name in [child_collection_name, parent_collection_name]:
        _drop_if_exists(vector_database, collection_name)

    for collection_name in [chat_collection_name, conversations_collection_name, user_collections_name]:
        _drop_if_exists(chat_database, collection_name)

    print("\nDeletion complete. Re-run initialization script to recreate collections.")


if __name__ == "__main__":
    confirm = input(
        "This will delete vector + chat + user_collections data. Continue? (y/n): "
    ).strip().lower()
    if confirm == "y":
        delete_all_collections()
    else:
        print("Operation cancelled.")
