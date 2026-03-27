#!/usr/bin/env python3
"""
Initialize Astra DB collections used by this project.

Vector keyspace collections:
- Default_Child_Collection (vector-enabled)
- Default_Parent_Collection (non-vector)

Chat/metadata keyspace collections:
- chat_messages
- conversations
- user_collections

The script supports separate vector/chat keyspaces or a shared keyspace.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from astrapy import DataAPIClient
from astrapy.constants import DefaultIdType, VectorMetric
from astrapy.info import (
    CollectionDefaultIDOptions,
    CollectionDefinition,
    CollectionLexicalOptions,
    CollectionVectorOptions,
)
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise KeyError(name)
    return value


def _optional(name: str) -> str | None:
    value = str(os.getenv(name) or "").strip()
    return value or None


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


def _ensure_collection(database: Any, name: str, definition: CollectionDefinition | None = None):
    existing_names = set(database.list_collection_names())
    if name in existing_names:
        print(f"Collection already exists: {name}")
        return database.get_collection(name)

    print(f"Creating collection: {name}")
    if definition is None:
        coll = database.create_collection(name)
    else:
        coll = database.create_collection(name, definition=definition)
    print(f"Created: {name}")
    return coll


def _child_collection_definition() -> CollectionDefinition:
    english_lexical_options = CollectionLexicalOptions(
        enabled=True,
        analyzer={
            "tokenizer": {"name": "standard"},
            "filters": [
                {"name": "lowercase"},
                {"name": "porterstem"},
                {"name": "asciifolding"},
                {"name": "stop"},
            ],
        },
    )
    return CollectionDefinition(
        lexical=english_lexical_options,
        vector=CollectionVectorOptions(
            dimension=768,
            metric=VectorMetric.COSINE,
        ),
        indexing={
            "allow": [
                "metadata.user_id",
                "metadata.file_metadata.file_name",
                "metadata.file_metadata.file_id",
                "metadata.collection_metadata.collection_id",
                "metadata.collection_metadata.collection_name",
                "metadata.child_chunk_metadata.parent_id",
                "metadata.child_chunk_metadata.child_chunk_number",
            ]
        },
        default_id=CollectionDefaultIDOptions(default_id_type=DefaultIdType.UUIDV6),
    )


def _parent_collection_definition() -> CollectionDefinition:
    return CollectionDefinition(
        indexing={
            "allow": [
                "value.metadata.user_id",
                "value.metadata.file_metadata.file_name",
                "value.metadata.file_metadata.file_id",
                "value.metadata.collection_metadata.collection_id",
                "value.metadata.collection_metadata.collection_name",
                "value.metadata.parent_chunk_metadata.parent_chunk_number",
            ]
        },
        default_id=CollectionDefaultIDOptions(default_id_type=DefaultIdType.UUIDV6),
    )


def _chat_messages_definition() -> CollectionDefinition:
    return CollectionDefinition(
        indexing={
            "allow": [
                "conversationId",
                "userId",
                "userEmail",
                "role",
                "timestamp",
            ]
        },
        default_id=CollectionDefaultIDOptions(default_id_type=DefaultIdType.UUIDV6),
    )


def _conversations_definition() -> CollectionDefinition:
    return CollectionDefinition(
        indexing={
            "allow": [
                "conversationId",
                "userId",
                "userEmail",
                "title",
                "updatedAt",
                "createdAt",
            ]
        },
        default_id=CollectionDefaultIDOptions(default_id_type=DefaultIdType.UUIDV6),
    )


def _user_collections_definition() -> CollectionDefinition:
    return CollectionDefinition(
        indexing={
            "allow": [
                "collection_id",
                "user_id",
                "name",
                "normalized_name",
                "is_default",
                "file_count",
                "updated_at",
                "created_at",
            ]
        },
        default_id=CollectionDefaultIDOptions(default_id_type=DefaultIdType.UUIDV6),
    )


def main() -> int:
    child_name = (
        _optional("ASTRA_CHILD_COLLECTION")
        or _optional("CHILD_COLLECTION_NAME")
        or "Default_Child_Collection"
    )
    parent_name = (
        _optional("ASTRA_PARENT_COLLECTION")
        or _optional("PARENT_COLLECTION_NAME")
        or "Default_Parent_Collection"
    )
    chat_messages_name = _optional("ASTRA_CHAT_COLLECTION") or "chat_messages"
    conversations_name = _optional("ASTRA_CONVERSATIONS_COLLECTION") or "conversations"
    user_collections_name = _optional("ASTRA_USER_COLLECTIONS_COLLECTION") or "user_collections"

    vector_database = _get_vector_database()
    chat_database = _get_chat_database()

    _ensure_collection(vector_database, child_name, _child_collection_definition())
    _ensure_collection(vector_database, parent_name, _parent_collection_definition())

    _ensure_collection(chat_database, chat_messages_name, _chat_messages_definition())
    _ensure_collection(chat_database, conversations_name, _conversations_definition())
    _ensure_collection(chat_database, user_collections_name, _user_collections_definition())

    print("\nInitialization complete.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyError as exc:
        missing = str(exc.args[0])
        print(f"Missing required environment variable: {missing}", file=sys.stderr)
        raise SystemExit(2)
