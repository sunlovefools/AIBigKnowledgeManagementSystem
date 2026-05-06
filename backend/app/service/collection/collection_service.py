"""Service layer for logical user collections/folders."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.db_dependencies import get_user_collections_collection
from app.core.id_utils import generate_uuid_v6


class CollectionServiceError(RuntimeError):
    """Base error for collection service failures."""


class CollectionNotFoundError(CollectionServiceError):
    """Raised when a user-scoped collection cannot be found."""


class CollectionConflictError(CollectionServiceError):
    """Raised when a create/rename violates uniqueness constraints."""


class ProtectedCollectionError(CollectionServiceError):
    """Raised when attempting an unsupported operation on protected collection."""


class CollectionService:
    """Operations for CRUD and scoping of logical user collections."""

    DEFAULT_COLLECTION_NAME = "Default"
    MAX_COLLECTION_NAME_LENGTH = 120 # Arbitrary limit to prevent excessively long names that could cause issues

    @staticmethod
    def _now_iso() -> str:
        """Get the current UTC time as an ISO 8601 string."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_user_id(user_id: str) -> str:
        """Normalize and validate user ID."""
        normalized = str(user_id or "").strip()
        if not normalized:
            raise ValueError("user_id must be a non-empty string.")
        return normalized

    @staticmethod
    def _normalize_collection_id(collection_id: str) -> str:
        """Normalize and validate collection ID."""
        normalized = str(collection_id or "").strip()
        if not normalized:
            raise ValueError("collection_id must be a non-empty string.")
        return normalized

    @staticmethod
    def _normalize_collection_name(name: str) -> tuple[str, str]:
        """Normalize and validate collection name. Returns a tuple of (cleaned_name, normalized_name)."""
        cleaned = str(name or "").strip()
        if not cleaned:
            raise ValueError("Collection name must not be empty.")
        if len(cleaned) > CollectionService.MAX_COLLECTION_NAME_LENGTH:
            raise ValueError(
                f"Collection name must not exceed {CollectionService.MAX_COLLECTION_NAME_LENGTH} characters."
            )
        return cleaned, cleaned.casefold() # Case folding for normalization to allow case-insensitive uniqueness

    @staticmethod
    def _get_store() -> Any:
        """Get the underlying data store for collections. Raises if unavailable."""
        store = get_user_collections_collection()
        if store is None:
            raise CollectionServiceError("Collection metadata store is unavailable.")
        return store

    @staticmethod
    def _sort_key_for_collection(item: dict[str, Any]) -> tuple[str, str]:
        created_at = str(item.get("created_at") or "")
        collection_id = str(item.get("collection_id") or "")
        return created_at, collection_id

    @staticmethod
    def _normalize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        """Normalize a raw collection row from the database into a consistent dict format. Returns None if invalid."""
        if not isinstance(row, dict):
            return None
        collection_id = str(row.get("collection_id") or row.get("collectionId") or "").strip()
        user_id = str(row.get("user_id") or row.get("userId") or "").strip()
        name = str(row.get("name") or "").strip()
        normalized_name = str(row.get("normalized_name") or row.get("normalizedName") or "").strip()

        # Basic validation to ensure that the collection have the required fields
        if not (collection_id and user_id and name):
            return None

        file_count_raw = row.get("file_count", 0)
        if isinstance(file_count_raw, bool):
            file_count = int(file_count_raw)
        elif isinstance(file_count_raw, (int, float)):
            file_count = int(file_count_raw)
        else:
            file_count = 0

        # Return a normalized dict with consistent field names and types
        return {
            "collection_id": collection_id,
            "user_id": user_id,
            "name": name,
            "normalized_name": normalized_name or name.casefold(),
            "is_default": bool(row.get("is_default", False)),
            "file_count": max(file_count, 0),
            "created_at": str(row.get("created_at") or row.get("createdAt") or ""),
            "updated_at": str(row.get("updated_at") or row.get("updatedAt") or ""),
        }

    @staticmethod
    async def ensure_default_collection(user_id: str) -> dict[str, Any]:
        """Ensure that a default collection exists for the user. Returns the default collection."""
        normalized_user_id = CollectionService._normalize_user_id(user_id)
        store = CollectionService._get_store()

        def _ensure() -> dict[str, Any]:
            all_rows = [
                normalized
                for raw in store.find({"user_id": normalized_user_id})
                if (normalized := CollectionService._normalize_row(raw)) is not None
            ]
            default_rows = [row for row in all_rows if bool(row.get("is_default"))]
            default_rows.sort(key=CollectionService._sort_key_for_collection)
            if default_rows:
                canonical = default_rows[0]
                for duplicate in default_rows[1:]:
                    store.delete_one(
                        {"user_id": normalized_user_id, "collection_id": duplicate["collection_id"]}
                    )
                return canonical

            now = CollectionService._now_iso()
            default_row = {
                "collection_id": generate_uuid_v6(),
                "user_id": normalized_user_id,
                "name": CollectionService.DEFAULT_COLLECTION_NAME,
                "normalized_name": CollectionService.DEFAULT_COLLECTION_NAME.casefold(),
                "is_default": True,
                "file_count": 0,
                "created_at": now,
                "updated_at": now,
            }
            store.insert_one(default_row)

            # Handle creation races by re-reading and deduplicating defaults.
            post_insert_rows = [
                normalized
                for raw in store.find({"user_id": normalized_user_id, "is_default": True})
                if (normalized := CollectionService._normalize_row(raw)) is not None
            ]
            post_insert_rows.sort(key=CollectionService._sort_key_for_collection)
            if not post_insert_rows:
                return default_row

            canonical = post_insert_rows[0]
            for duplicate in post_insert_rows[1:]:
                store.delete_one(
                    {"user_id": normalized_user_id, "collection_id": duplicate["collection_id"]}
                )
            return canonical

        return await asyncio.to_thread(_ensure)

    @staticmethod
    async def list_collections(user_id: str) -> list[dict[str, Any]]:
        normalized_user_id = CollectionService._normalize_user_id(user_id)
        await CollectionService.ensure_default_collection(normalized_user_id)
        store = CollectionService._get_store()

        def _list() -> list[dict[str, Any]]:
            rows_by_id: dict[str, dict[str, Any]] = {}
            for row in store.find({"user_id": normalized_user_id}):
                normalized = CollectionService._normalize_row(row)
                if normalized is not None:
                    rows_by_id[normalized["collection_id"]] = normalized
            rows = list(rows_by_id.values())
            rows.sort(key=lambda item: (not bool(item.get("is_default")), item["name"].casefold()))
            return rows

        return await asyncio.to_thread(_list)

    @staticmethod
    async def get_collection(user_id: str, collection_id: str) -> dict[str, Any]:
        normalized_user_id = CollectionService._normalize_user_id(user_id)
        normalized_collection_id = CollectionService._normalize_collection_id(collection_id)
        store = CollectionService._get_store()

        def _get() -> dict[str, Any] | None:
            row = store.find_one(
                {"user_id": normalized_user_id, "collection_id": normalized_collection_id}
            )
            return CollectionService._normalize_row(row)

        collection = await asyncio.to_thread(_get)
        if collection is None:
            raise CollectionNotFoundError(
                f"Collection '{normalized_collection_id}' not found for this user."
            )
        return collection

    @staticmethod
    async def resolve_active_collection(
        user_id: str,
        requested_collection_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_user_id = CollectionService._normalize_user_id(user_id)
        default_collection = await CollectionService.ensure_default_collection(normalized_user_id)
        normalized_requested = str(requested_collection_id or "").strip()
        if not normalized_requested:
            return default_collection
        if normalized_requested == str(default_collection["collection_id"]):
            return default_collection
        return await CollectionService.get_collection(normalized_user_id, normalized_requested)

    @staticmethod
    async def create_collection(user_id: str, name: str) -> dict[str, Any]:
        normalized_user_id = CollectionService._normalize_user_id(user_id)
        cleaned_name, normalized_name = CollectionService._normalize_collection_name(name)
        await CollectionService.ensure_default_collection(normalized_user_id)
        store = CollectionService._get_store()

        def _create() -> dict[str, Any]:
            conflict = store.find_one(
                {"user_id": normalized_user_id, "normalized_name": normalized_name}
            )
            if CollectionService._normalize_row(conflict) is not None:
                raise CollectionConflictError(
                    f"A collection named '{cleaned_name}' already exists."
                )

            now = CollectionService._now_iso()
            row = {
                "collection_id": generate_uuid_v6(),
                "user_id": normalized_user_id,
                "name": cleaned_name,
                "normalized_name": normalized_name,
                "is_default": False,
                "file_count": 0,
                "created_at": now,
                "updated_at": now,
            }
            store.insert_one(row)
            return row

        return await asyncio.to_thread(_create)

    @staticmethod
    async def _rename_collection_metadata_in_parent_chunks(
        *,
        user_id: str,
        collection_id: str,
        new_name: str,
    ) -> None:
        from app.vectordb.vectordb import PARENT_STORE

        def _rename() -> None:
            filter_doc = {
                "value.metadata.user_id": user_id,
                "value.metadata.collection_metadata.collection_id": collection_id,
            }
            for row in PARENT_STORE.collection.find(filter_doc):
                if not isinstance(row, dict):
                    continue
                parent_id = str(row.get("_id") or "").strip()
                value = row.get("value")
                if not parent_id or not isinstance(value, dict):
                    continue

                metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
                collection_meta = (
                    metadata.get("collection_metadata")
                    if isinstance(metadata.get("collection_metadata"), dict)
                    else {}
                )
                collection_meta["collection_id"] = collection_id
                collection_meta["collection_name"] = new_name
                metadata["collection_metadata"] = collection_meta
                value["metadata"] = metadata
                PARENT_STORE.collection.replace_one(
                    {"_id": parent_id},
                    {"_id": parent_id, "value": value},
                    upsert=True,
                )

        await asyncio.to_thread(_rename)

    @staticmethod
    async def _rename_collection_metadata_in_child_chunks(
        *,
        user_id: str,
        collection_id: str,
        new_name: str,
    ) -> None:
        from app.vectordb.vectordb import VECTOR_STORE

        def _rename() -> None:
            child_collection = getattr(VECTOR_STORE, "collection", None)
            if child_collection is None:
                return

            filter_doc = {
                "metadata.user_id": user_id,
                "metadata.collection_metadata.collection_id": collection_id,
            }
            for row in child_collection.find(filter_doc):
                if not isinstance(row, dict):
                    continue
                row_id = str(row.get("_id") or "").strip()
                if not row_id:
                    continue

                metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                collection_meta = (
                    metadata.get("collection_metadata")
                    if isinstance(metadata.get("collection_metadata"), dict)
                    else {}
                )
                collection_meta["collection_id"] = collection_id
                collection_meta["collection_name"] = new_name
                metadata["collection_metadata"] = collection_meta
                row["metadata"] = metadata
                child_collection.replace_one(
                    filter={"_id": row_id},
                    replacement=row,
                    upsert=True,
                )

        await asyncio.to_thread(_rename)

    @staticmethod
    async def rename_collection(user_id: str, collection_id: str, new_name: str) -> dict[str, Any]:
        normalized_user_id = CollectionService._normalize_user_id(user_id)
        normalized_collection_id = CollectionService._normalize_collection_id(collection_id)
        cleaned_name, normalized_name = CollectionService._normalize_collection_name(new_name)
        current = await CollectionService.get_collection(normalized_user_id, normalized_collection_id)
        store = CollectionService._get_store()

        if (
            current["normalized_name"] == normalized_name
            and current["name"] == cleaned_name
        ):
            return current

        def _rename_row() -> dict[str, Any]:
            conflict = store.find_one(
                {"user_id": normalized_user_id, "normalized_name": normalized_name}
            )
            normalized_conflict = CollectionService._normalize_row(conflict)
            if (
                normalized_conflict is not None
                and normalized_conflict["collection_id"] != normalized_collection_id
            ):
                raise CollectionConflictError(
                    f"A collection named '{cleaned_name}' already exists."
                )

            now = CollectionService._now_iso()
            update_doc = {
                "$set": {
                    "name": cleaned_name,
                    "normalized_name": normalized_name,
                    "updated_at": now,
                }
            }
            store.update_one(
                {
                    "user_id": normalized_user_id,
                    "collection_id": normalized_collection_id,
                },
                update_doc,
            )
            updated = store.find_one(
                {"user_id": normalized_user_id, "collection_id": normalized_collection_id}
            )
            normalized_updated = CollectionService._normalize_row(updated)
            if normalized_updated is None:
                raise CollectionNotFoundError(
                    f"Collection '{normalized_collection_id}' not found for this user."
                )
            return normalized_updated

        updated_collection = await asyncio.to_thread(_rename_row)
        await CollectionService._rename_collection_metadata_in_parent_chunks(
            user_id=normalized_user_id,
            collection_id=normalized_collection_id,
            new_name=cleaned_name,
        )
        await CollectionService._rename_collection_metadata_in_child_chunks(
            user_id=normalized_user_id,
            collection_id=normalized_collection_id,
            new_name=cleaned_name,
        )
        return updated_collection

    @staticmethod
    def _extract_file_id_from_parent_row(row: dict[str, Any]) -> str:
        value = row.get("value")
        if not isinstance(value, dict):
            return ""
        metadata = value.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        file_metadata = metadata.get("file_metadata")
        if not isinstance(file_metadata, dict):
            file_metadata = {}
        return str(file_metadata.get("file_id") or metadata.get("file_id") or "").strip()

    @staticmethod
    def _extract_collection_id_from_metadata(
        metadata: dict[str, Any],
    ) -> str:
        collection_meta = metadata.get("collection_metadata")
        if not isinstance(collection_meta, dict):
            return ""
        return str(collection_meta.get("collection_id") or "").strip()

    @staticmethod
    async def list_file_ids_for_collection(
        user_id: str,
        collection_id: str,
    ) -> list[str]:
        from app.vectordb.vectordb import PARENT_STORE

        normalized_user_id = CollectionService._normalize_user_id(user_id)
        active_collection = await CollectionService.resolve_active_collection(
            normalized_user_id, collection_id
        )
        active_collection_id = str(active_collection["collection_id"])
        default_collection = await CollectionService.ensure_default_collection(normalized_user_id)
        default_collection_id = str(default_collection["collection_id"])

        def _list() -> list[str]:
            file_ids: set[str] = set()
            filter_doc = {"value.metadata.user_id": normalized_user_id}
            for row in PARENT_STORE.collection.find(filter_doc):
                if not isinstance(row, dict):
                    continue
                value = row.get("value")
                if not isinstance(value, dict):
                    continue
                metadata = value.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}

                row_collection_id = CollectionService._extract_collection_id_from_metadata(metadata)
                if not row_collection_id:
                    row_collection_id = default_collection_id
                if row_collection_id != active_collection_id:
                    continue

                file_id = CollectionService._extract_file_id_from_parent_row(row)
                if file_id:
                    file_ids.add(file_id)
            return sorted(file_ids)

        return await asyncio.to_thread(_list)

    @staticmethod
    async def _set_collection_file_count(
        *,
        user_id: str,
        collection_id: str,
        file_count: int,
    ) -> None:
        store = CollectionService._get_store()

        def _set() -> None:
            store.update_one(
                {"user_id": user_id, "collection_id": collection_id},
                {"$set": {"file_count": max(int(file_count), 0), "updated_at": CollectionService._now_iso()}},
            )

        await asyncio.to_thread(_set)

    @staticmethod
    async def reconcile_all_collection_file_counts(user_id: str) -> None:
        normalized_user_id = CollectionService._normalize_user_id(user_id)
        collections = await CollectionService.list_collections(normalized_user_id)
        for collection in collections:
            collection_id = str(collection["collection_id"])
            file_ids = await CollectionService.list_file_ids_for_collection(
                normalized_user_id,
                collection_id,
            )
            await CollectionService._set_collection_file_count(
                user_id=normalized_user_id,
                collection_id=collection_id,
                file_count=len(file_ids),
            )

    @staticmethod
    async def delete_collection(user_id: str, collection_id: str) -> dict[str, Any]:
        from app.vectordb.vectordb import (
            delete_children_by_file_id,
            delete_parent_documents_by_file_id,
        )

        normalized_user_id = CollectionService._normalize_user_id(user_id)
        normalized_collection_id = CollectionService._normalize_collection_id(collection_id)
        target = await CollectionService.get_collection(normalized_user_id, normalized_collection_id)
        if bool(target.get("is_default")):
            raise ProtectedCollectionError("Default collection cannot be deleted.")

        file_ids = await CollectionService.list_file_ids_for_collection(
            normalized_user_id,
            normalized_collection_id,
        )

        deleted_parent_chunks = 0
        deleted_child_chunks = 0
        warnings: list[str] = []

        for file_id in file_ids:
            try:
                deleted_children = await delete_children_by_file_id(file_id, normalized_user_id)
                deleted_parents = await delete_parent_documents_by_file_id(file_id, normalized_user_id)
                deleted_child_chunks += int(deleted_children or 0)
                deleted_parent_chunks += int(deleted_parents or 0)
            except Exception as error:
                warnings.append(f"Failed to cascade-delete file '{file_id}': {error}")

        store = CollectionService._get_store()

        def _delete_row() -> None:
            store.delete_one(
                {
                    "user_id": normalized_user_id,
                    "collection_id": normalized_collection_id,
                }
            )

        await asyncio.to_thread(_delete_row)
        await CollectionService.reconcile_all_collection_file_counts(normalized_user_id)

        return {
            "collection_id": normalized_collection_id,
            "name": str(target.get("name") or ""),
            "deleted_files": len(file_ids),
            "deleted_parent_chunks": deleted_parent_chunks,
            "deleted_child_chunks": deleted_child_chunks,
            "warnings": warnings,
        }

    @staticmethod
    def apply_collection_metadata_to_chunks(
        *,
        parent_chunks: list[dict[str, Any]],
        child_chunks: list[dict[str, Any]],
        collection_id: str,
        collection_name: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        normalized_collection_id = CollectionService._normalize_collection_id(collection_id)
        normalized_collection_name = str(collection_name or "").strip() or CollectionService.DEFAULT_COLLECTION_NAME
        collection_metadata = {
            "collection_id": normalized_collection_id,
            "collection_name": normalized_collection_name,
        }

        for parent_chunk in parent_chunks:
            if isinstance(parent_chunk, dict):
                parent_chunk["collection_metadata"] = dict(collection_metadata)
        for child_chunk in child_chunks:
            if isinstance(child_chunk, dict):
                child_chunk["collection_metadata"] = dict(collection_metadata)
        return parent_chunks, child_chunks

    @staticmethod
    async def resolve_collection_metadata_for_row(
        *,
        user_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, str]:
        normalized_user_id = CollectionService._normalize_user_id(user_id)
        default_collection = await CollectionService.ensure_default_collection(normalized_user_id)
        default_id = str(default_collection["collection_id"])
        default_name = str(default_collection["name"])

        if not isinstance(metadata, dict):
            return {"collection_id": default_id, "collection_name": default_name}

        raw_collection_meta = metadata.get("collection_metadata")
        if not isinstance(raw_collection_meta, dict):
            return {"collection_id": default_id, "collection_name": default_name}

        collection_id = str(raw_collection_meta.get("collection_id") or "").strip()
        collection_name = str(raw_collection_meta.get("collection_name") or "").strip()
        if not collection_id:
            return {"collection_id": default_id, "collection_name": default_name}

        try:
            collection = await CollectionService.get_collection(normalized_user_id, collection_id)
            return {
                "collection_id": str(collection["collection_id"]),
                "collection_name": str(collection["name"]),
            }
        except CollectionNotFoundError:
            return {"collection_id": default_id, "collection_name": default_name}
        except Exception:
            if collection_name:
                return {"collection_id": collection_id, "collection_name": collection_name}
            return {"collection_id": default_id, "collection_name": default_name}
