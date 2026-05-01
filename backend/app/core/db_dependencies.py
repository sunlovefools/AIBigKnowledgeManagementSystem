"""
This module provides functions to access the Astra DB collections for conversations and chat messages, using environment variables for configuration. 
The database client and collection references are cached for efficient reuse across API requests.
"""

import os
from functools import lru_cache
from typing import Any

from astrapy import DataAPIClient


def _optional_env(name: str) -> str | None:
    """
    Return a normalized optional env value.

    Values that are empty or comment placeholders are treated as missing.
    """
    raw = os.getenv(name)
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if value.startswith("#"):
        return None
    return value


@lru_cache(maxsize=1)
def get_chat_database() -> Any | None:
    """Initializes and returns the Astra DB database client using environment variables for configuration."""
    astra_db_url = _optional_env("ASTRA_CHAT_DB_URL") or _optional_env("ASTRA_DB_URL")
    astra_db_token = _optional_env("ASTRA_CHAT_DB_TOKEN") or _optional_env("ASTRA_DB_TOKEN")
    # Conversation/chat collections should live in the dedicated history keyspace.
    astra_db_keyspace = (
        _optional_env("ASTRA_CHAT_KEYSPACE")
        or "conversation_history"
    )

    if not astra_db_url or not astra_db_token:
        return None

    client = DataAPIClient()
    return client.get_database(astra_db_url, token=astra_db_token, keyspace=astra_db_keyspace)


@lru_cache(maxsize=1)
def get_conversations_collection() -> Any | None:
    """Initializes and returns the Astra DB collection for conversations."""
    database = get_chat_database()
    if database is None:
        return None

    conversations_collection_name = os.getenv("ASTRA_CONVERSATIONS_COLLECTION", "conversations")
    return database.get_collection(conversations_collection_name)


@lru_cache(maxsize=1)
def get_chat_messages_collection() -> Any | None:
    """Initializes and returns the Astra DB collection for chat messages."""
    database = get_chat_database()
    if database is None:
        return None

    chat_collection_name = os.getenv("ASTRA_CHAT_COLLECTION", "chat_messages")
    return database.get_collection(chat_collection_name)


@lru_cache(maxsize=1)
def get_user_collections_collection() -> Any | None:
    """Initializes and returns the Astra DB collection for user logical collections/folders."""
    database = get_chat_database()
    if database is None:
        return None

    user_collections_name = os.getenv("ASTRA_USER_COLLECTIONS_COLLECTION", "user_collections")
    return database.get_collection(user_collections_name)
