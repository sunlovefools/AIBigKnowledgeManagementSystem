#!/usr/bin/env python3
"""
One-time migration: remove legacy local-auth users for Auth0-only cutover.

Usage:
  python backend/scripts/migrate_auth0_only_remove_local_users.py
  python backend/scripts/migrate_auth0_only_remove_local_users.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from dotenv import load_dotenv
from astrapy import DataAPIClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete users where auth_provider == 'local' from Astra users collection."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count matching local users without deleting.",
    )
    parser.add_argument(
        "--collection",
        default="users",
        help="Users collection name (default: users).",
    )
    return parser.parse_args()


def _deleted_count(delete_result: object) -> int:
    if delete_result is None:
        return 0

    deleted_count = getattr(delete_result, "deleted_count", None)
    if isinstance(deleted_count, int):
        return deleted_count

    if isinstance(delete_result, dict):
        status = delete_result.get("status")
        if isinstance(status, dict):
            maybe_deleted = status.get("deletedCount")
            if isinstance(maybe_deleted, int):
                return maybe_deleted
    return 0


def main() -> int:
    args = parse_args()
    load_dotenv()

    endpoint = os.getenv("ASTRA_DB_URL")
    token = os.getenv("ASTRA_DB_TOKEN")
    if not endpoint or not token:
        print("Missing ASTRA_DB_URL or ASTRA_DB_TOKEN in environment.", file=sys.stderr)
        return 2

    client = DataAPIClient()
    database = client.get_database(endpoint, token=token)
    collection_name = str(args.collection or "users")
    filter_doc = {"auth_provider": "local"}

    existing_names = set(database.list_collection_names())
    if collection_name not in existing_names:
        print(f"Collection '{collection_name}' does not exist. Nothing to migrate.")
        return 0

    collection = database.get_collection(collection_name)
    matching_rows = [row for row in collection.find(filter_doc) if isinstance(row, dict)]
    match_count = len(matching_rows)
    print(f"Found {match_count} local user(s) in '{collection_name}'.")

    if args.dry_run:
        print("Dry run complete. No users deleted.")
        return 0

    if match_count == 0:
        print("No local users to delete. Migration is idempotent and complete.")
        return 0

    deleted_total = 0
    if hasattr(collection, "delete_many"):
        delete_result = collection.delete_many(filter_doc)
        deleted_total = _deleted_count(delete_result)

    # Fallback for clients where delete_many is unavailable or returns unknown count.
    if deleted_total == 0:
        for row in matching_rows:
            row_id = str(row.get("_id") or "").strip()
            if not row_id:
                continue
            delete_result = collection.delete_one({"_id": row_id})
            deleted_total += _deleted_count(delete_result)

    print(f"Deleted {deleted_total} local user(s).")
    print("Auth0-only user migration complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
