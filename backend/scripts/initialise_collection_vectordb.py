#!/usr/bin/env python3
"""
Initialize two Astra DB collections:

1) Default_Child_Collection
   - Vector-enabled (dimension=768, metric=cosine)
   - Selective indexing on:
       - file_metadata.file_id
       - child_chunk_metadata.child_chunk_number
       - child_chunk_metadata.parent_id (to maintain Parent-Child relationship)

2) Default_Parent_Collection
   - Non-vector collection
   - Selective indexing on:
       - file_metadata.file_id
       - parent_chunk_metadata.parent_chunk_number

Env vars required:
  ASTRA_DB_URL
  ASTRA_DB_TOKEN

Optional:
  ASTRA_DB_KEYSPACE
  ASTRA_CHILD_COLLECTION (default: Default_Child_Collection)
  ASTRA_PARENT_COLLECTION (default: Default_Parent_Collection)

Notes:
- For embeddings, Astra stores vectors in the reserved "$vector" field in documents.
- Indexing "allow" means only those paths are indexed for filtering/sorting.
"""

from __future__ import annotations

import os
import sys
from dotenv import load_dotenv
from astrapy import DataAPIClient
from astrapy.constants import VectorMetric, DefaultIdType
from astrapy.info import (
    CollectionDefinition,
    CollectionVectorOptions,
    CollectionDefaultIDOptions,
)

load_dotenv()
def get_database():
    endpoint = os.environ["ASTRA_DB_URL"]
    token = os.environ["ASTRA_DB_TOKEN"]
    keyspace = "default_keyspace"

    client = DataAPIClient()
    if keyspace:
        return client.get_database(endpoint, token=token, keyspace=keyspace)
    return client.get_database(endpoint, token=token)


def ensure_collection(database, name: str, definition: CollectionDefinition):
    existing_names = set(database.list_collection_names())
    if name in existing_names:
        print(f"✓ Collection already exists: {name}")
        return database.get_collection(name)

    print(f"+ Creating collection: {name}")
    coll = database.create_collection(name, definition=definition)
    print(f"✓ Created: {name}")
    return coll


def main() -> int:
    child_name = os.getenv("ASTRA_CHILD_COLLECTION", "Default_Child_Collection")
    parent_name = os.getenv("ASTRA_PARENT_COLLECTION", "Default_Parent_Collection")

    database = get_database()

    # --- Child (vector) collection definition ---
    child_definition = CollectionDefinition(
        vector=CollectionVectorOptions(
            dimension=768,
            metric=VectorMetric.COSINE,
        ),
        indexing={
            "allow": [
                "metadata.file_metadata.file_name",
                "metadata.file_metadata.file_id",
                "metadata.child_chunk_metadata.parent_id",
                "metadata.child_chunk_metadata.child_chunk_number",
            ]
        },
        default_id=CollectionDefaultIDOptions(default_id_type=DefaultIdType.UUIDV6),
    )

    # --- Parent (non-vector) collection definition ---
    parent_definition = CollectionDefinition(
        indexing={
            "allow": [
                "value.metadata.file_metadata.file_name",
                "value.metadata.file_metadata.file_id",
                "value.metadata.parent_chunk_metadata.parent_chunk_number",
            ]
        },
        default_id=CollectionDefaultIDOptions(default_id_type=DefaultIdType.UUIDV6),
    )

    ensure_collection(database, child_name, child_definition)
    ensure_collection(database, parent_name, parent_definition)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyError as e:
        missing = e.args[0]
        print(f"Missing required environment variable: {missing}", file=sys.stderr)
        return_code = 2
        raise SystemExit(return_code)