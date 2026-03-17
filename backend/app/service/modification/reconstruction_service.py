"""
Module for handling file reconstruction and modification operations.
Provides functionality to retrieve all uploaded documents and reconstruct them
from their stored chunks via the Parent-Child RAG pattern.
"""

import asyncio
import difflib
import traceback
from typing import Any, Literal

from app.vectordb.vectordb import (
    PARENT_STORE,
    delete_children_by_file_id,
    delete_children_by_parent_id,
    delete_parent_documents_by_file_id,
    delete_parent_document,
    upsert_documents,
)
from app.service.rag.ingestion.markdown_chunker import split_parent_child_chunks_from_markdown
from app.service.rag.ingestion.markdown_canonicalizer import (
    normalize_markdown_for_modification,
)
from app.service.rag.ingestion.chunk_polisher import polish_chunks
from app.service.storage.s3_image_store import delete_docling_artifacts_by_file_id
from debug.debug_logger import log_vector_db_result


class ReconstructionService:
    """A wrapper service for reconstructing files from stored chunks."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_preview(text: str, max_chars: int = 240) -> str:
        """Build a compact single-line preview snippet."""
        if not text:
            return ""
        cleaned = " ".join(str(text).split())  # collapse whitespace/newlines
        return cleaned[:max_chars]

    @staticmethod
    def _extract_parent_row_fields(row: dict) -> dict[str, Any] | None:
        """
        Extract common fields from a raw Astra parent-collection row and return a structured dictionary.
        Returns None if row is not usable.
        """
        # TODO: This extraction method loses a lot of metadata, we may need to preserve more of it in the future
        if not isinstance(row, dict):
            return None

        parent_id = str(row.get("_id", "")).strip()
        parent_doc = row.get("value")
        if not isinstance(parent_doc, dict):
            return None

        metadata = parent_doc.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}

        file_metadata = metadata.get("file_metadata") or {}
        if not isinstance(file_metadata, dict):
            file_metadata = {}

        parent_chunk_metadata = metadata.get("parent_chunk_metadata") or {}
        if not isinstance(parent_chunk_metadata, dict):
            parent_chunk_metadata = {}

        file_id = str(file_metadata.get("file_id") or "").strip()
        file_name = file_metadata.get("file_name") or metadata.get("source") or "Unknown"
        content = str(parent_doc.get("page_content", "") or "")
        chunk_number = parent_chunk_metadata.get("parent_chunk_number")

        chunk_number_int: int | None = None
        if isinstance(chunk_number, (int, float)):
            chunk_number_int = int(chunk_number)

        raw_page_numbers = parent_chunk_metadata.get("page_number")
        page_numbers: list[int] = []
        if isinstance(raw_page_numbers, list):
            for value in raw_page_numbers:
                if isinstance(value, (int, float)):
                    page_numbers.append(int(value))
        elif isinstance(raw_page_numbers, (int, float)):
            page_numbers.append(int(raw_page_numbers))

        if not page_numbers:
            page_numbers = [0]

        return {
            "parentId": parent_id,
            "fileId": file_id,
            "fileName": str(file_name),
            "content": content,
            "chunkNumber": chunk_number_int,
            "pageNumbers": page_numbers,
        }

    @staticmethod
    async def _find_first_parent_row_for_file_id(file_id: str) -> dict | None:
        """
        Fetch a single parent row for a file ID.

        File-level operations only need one representative row to validate
        existence and recover the human-readable file name.
        """

        def _find_one() -> dict | None:
            collection = PARENT_STORE.collection
            cursor = collection.find({"value.metadata.file_metadata.file_id": file_id})
            for row in cursor:
                if isinstance(row, dict):
                    return row
            return None

        return await asyncio.to_thread(_find_one)

    # ------------------------------------------------------------------
    # Pagination: parent chunks per fileId
    # ------------------------------------------------------------------

    @staticmethod
    async def _find_parent_chunks_in_range(
        file_id: str, current_chunk_number: int, limit: int
    ) -> tuple[list[dict], bool, str | None]:
        """
        Find parent chunks for a fileId with deterministic ordering and cursor.
        Cursor is the last returned chunkNumber (int as string).
        """

        def _query_rows() -> list[dict]:
            collection = PARENT_STORE.collection
            # IMPORTANT:
            # Use only "$gt" + sort in application, then slice to `limit`.
            # The previous "$lt current+limit" + window_size=limit-1 can skip chunks.
            cursor = collection.find(
                {
                    "value.metadata.file_metadata.file_id": file_id,
                    "value.metadata.parent_chunk_metadata.parent_chunk_number": {
                        "$gt": current_chunk_number
                    },
                }
            )
            rows: list[dict] = []
            for row in cursor:
                if isinstance(row, dict):
                    rows.append(row)
            return rows

        rows = await asyncio.to_thread(_query_rows)

        sortable: list[dict] = []
        for row in rows:
            fields = ReconstructionService._extract_parent_row_fields(row)
            if not fields:
                continue
            chunk_num = fields["chunkNumber"]
            if chunk_num is None:
                # If chunkNumber is missing, push to the end deterministically.
                # This prevents them from breaking pagination ordering.
                chunk_num = 10**9

            sortable.append(
                {
                    "chunkNumber": int(chunk_num),
                    "parentId": fields["parentId"],
                    "content": fields["content"],
                    "size": len(fields["content"]),
                    "pageNumbers": fields["pageNumbers"],
                }
            )

        # Deterministic ordering: (chunkNumber, parentId)
        sortable.sort(key=lambda item: (item["chunkNumber"], item["parentId"]))

        page_rows = sortable[: max(limit, 1)]
        has_more = len(sortable) > len(page_rows)
        next_cursor = str(page_rows[-1]["chunkNumber"]) if has_more and page_rows else None

        chunks = [
            {
                "parentId": r["parentId"],
                "content": r["content"],
                "size": r["size"],
                "pageNumbers": r["pageNumbers"],
            }
            for r in page_rows
        ]
        return chunks, has_more, next_cursor

    # ------------------------------------------------------------------
    # Sidebar: merged file list
    # ------------------------------------------------------------------

    @staticmethod
    async def get_all_preview_files() -> list[dict]:
        """
        Retrieve a filename-merged file list for the sidebar.

        **Critical fix**: Collapse many parent-chunk rows into 1 item per fileId.
        """
        print("🔄 Retrieving filename-merged summaries from Parent Store...")

        try:
            rows = await PARENT_STORE.get_all_files()
            if not rows:
                return []

            # fileId -> best summary candidate (prefer smallest chunkNumber)
            by_file_id: dict[str, dict] = {}

            for row in rows:
                fields = ReconstructionService._extract_parent_row_fields(row)
                if not fields:
                    continue

                file_id = fields["fileId"]
                if not file_id:
                    # Skip rows with missing file_id — they can't be merged reliably.
                    continue

                file_name = fields["fileName"]
                preview = ReconstructionService._safe_preview(fields["content"])
                chunk_num = fields["chunkNumber"]
                # pick best representative: smallest chunkNumber, else tie by parentId
                candidate = {
                    "fileId": file_id,
                    "fileName": file_name,
                    "preview": preview,
                    "_chunkNumber": chunk_num if chunk_num is not None else 10**9,
                    "_parentId": fields["parentId"],
                }

                existing = by_file_id.get(file_id)
                if existing is None:
                    by_file_id[file_id] = candidate
                else:
                    # Keep whichever is "earlier" in the file
                    if (candidate["_chunkNumber"], candidate["_parentId"]) < (
                        existing["_chunkNumber"],
                        existing["_parentId"],
                    ):
                        by_file_id[file_id] = candidate

            # Final list: drop internal fields and stable sort
            summaries = [
                {
                    "fileId": v["fileId"],
                    "fileName": v["fileName"],
                    "preview": v["preview"],
                }
                for v in by_file_id.values()
            ]
            summaries.sort(key=lambda item: (str(item.get("fileName", "")).lower(), item.get("fileId", "")))

            log_vector_db_result(
                function_name="get_all_preview_files",
                context={
                    "totalRows": len(rows),
                    "uniqueFiles": len(summaries),
                },
                retrieved=summaries,
            )
            return summaries

        except RuntimeError:
            raise
        except Exception as error:
            print(f"❌ Failed to retrieve file summaries: {error}")
            traceback.print_exc()
            raise RuntimeError(f"File summary retrieval failed: {str(error)}")

    # ------------------------------------------------------------------
    # File chunks API
    # ------------------------------------------------------------------

    @staticmethod
    async def get_file_parent_chunks(file_id: str, limit: int, cursor: str | None) -> dict:
        """Retrieve paginated parent chunks for a merged file ID item."""
        print(f"🔄 Retrieving paginated parent chunks for file_id: {file_id}")

        try:
            current_chunk_number = -1
            if cursor:
                try:
                    current_chunk_number = int(cursor)
                except ValueError:
                    current_chunk_number = -1

            chunks, has_more, next_cursor = await ReconstructionService._find_parent_chunks_in_range(
                file_id=file_id,
                current_chunk_number=current_chunk_number,
                limit=limit,
            )

            result = {
                "fileId": file_id,
                "chunks": chunks,
                "hasMore": has_more,
                "nextCursor": next_cursor,
            }

            log_vector_db_result(
                function_name="get_file_parent_chunks",
                context={
                    "fileId": file_id,
                    "limit": limit,
                    "cursor": cursor,
                    "currentChunkNumber": current_chunk_number,
                    "returnedChunks": len(chunks),
                    "hasMore": has_more,
                    "nextCursor": next_cursor,
                },
                retrieved=result,
            )
            return result

        except RuntimeError:
            raise
        except Exception as error:
            print(f"❌ Failed to retrieve parent chunks for file_id={file_id}: {error}")
            traceback.print_exc()
            raise RuntimeError(f"File chunk retrieval failed: {str(error)}")

    # ------------------------------------------------------------------
    # Document and file updates
    # ------------------------------------------------------------------

    @staticmethod
    async def get_file_names_by_ids(file_ids: list[str]) -> dict[str, str]:
        """
        Return a fileId -> fileName mapping for a specific list of fileIds.
        Fetches only ONE parent chunk per fileId — avoids full-collection scans
        that can trigger AstraDB ClosedConnectionException on large stores.
        """
        result: dict[str, str] = {}
        for file_id in file_ids:
            if not file_id:
                continue
            try:
                row = await ReconstructionService._find_first_parent_row_for_file_id(file_id)
                if row:
                    fields = ReconstructionService._extract_parent_row_fields(row)
                    if fields:
                        result[file_id] = fields["fileName"]
            except Exception as error:
                print(f"⚠️  Could not resolve fileName for file_id={file_id}: {error}")
                result[file_id] = "unknown"
        return result

    @staticmethod
    async def delete_file(file_id: str) -> dict:
        """
        Delete one logical file from Astra and then attempt S3 cleanup.

        The database delete is authoritative. S3 cleanup is best-effort so a
        missing prefix or disabled upload setting does not resurrect the file.
        """
        print(f"Deleting file, file_id={file_id}...")

        try:
            row = await ReconstructionService._find_first_parent_row_for_file_id(file_id)
            if row is None:
                raise FileNotFoundError(f"No parent chunks found for file_id={file_id}")

            fields = ReconstructionService._extract_parent_row_fields(row) or {}
            file_name = str(fields.get("fileName") or "Unknown")

            deleted_child_chunks = await delete_children_by_file_id(file_id)
            deleted_parent_chunks = await delete_parent_documents_by_file_id(file_id)

            if deleted_parent_chunks <= 0:
                raise RuntimeError(
                    f"Parent chunk deletion removed no rows for file_id={file_id}"
                )

            s3_cleanup = delete_docling_artifacts_by_file_id(file_id)

            print(
                "Delete completed for file_id=%s (parents=%s children=%s s3=%s)"
                % (
                    file_id,
                    deleted_parent_chunks,
                    deleted_child_chunks,
                    s3_cleanup["s3Status"],
                )
            )
            return {
                "fileId": file_id,
                "fileName": file_name,
                "deletedParentChunks": deleted_parent_chunks,
                "deletedChildChunks": deleted_child_chunks,
                "s3Status": s3_cleanup["s3Status"],
                "s3DeletedObjects": s3_cleanup["s3DeletedObjects"],
                "warnings": list(s3_cleanup.get("warnings", [])),
            }
        except FileNotFoundError:
            raise
        except Exception as error:
            print(f"Failed to delete file {file_id}: {error}")
            traceback.print_exc()
            raise RuntimeError(f"File deletion failed: {str(error)}")

    @staticmethod
    async def get_document_by_id(parent_id: str) -> dict | None:
        """
        Look up a single parent chunk by its ID.
        Returns extracted fields (including 'fileName') or None if not found.
        Called by router_modifications before update_document to validate ownership.
        """
        try:
            def _find_row() -> dict | None:
                """
                Inner helper function called by the async wrapper to perform the blocking DB call in a thread.
                """
                collection = PARENT_STORE.collection
                cursor = collection.find({"_id": parent_id})
                for row in cursor:
                    if isinstance(row, dict):
                        return row
                return None

            # Asyncally run the blocking DB call in a thread to avoid FastAPI event loop issues.
            row = await asyncio.to_thread(_find_row)
            if row is None:
                return None
            return ReconstructionService._extract_parent_row_fields(row)
        
        except Exception as error:
            print(f"❌ Failed to look up document {parent_id}: {error}")
            traceback.print_exc()
            raise RuntimeError(f"Document lookup failed: {str(error)}")

    async def update_document(parent_id: str, new_content: str, file_name: str) -> dict:
        """
        Update a single parent chunk and its children, preserving the original file_id.
        (kept as you had it; unchanged except style)
        """
        print(f"📝 Updating document {parent_id} ({file_name})...")

        try:
            normalized_new_content = normalize_markdown_for_modification(new_content)
            existing_file_id: str | None = None
            try:
                old_doc = await PARENT_STORE.aget(parent_id)
                if isinstance(old_doc, dict):
                    existing_file_id = (
                        (old_doc.get("metadata") or {})
                        .get("file_metadata", {})
                        .get("file_id")
                    )
            except Exception:
                pass

            print("  → Step 1: Deleting old child chunks...")
            await delete_children_by_parent_id(parent_id)

            print("  → Step 2: Deleting old parent document...")
            await delete_parent_document(parent_id)

            print("  → Step 3: Re-chunking new content...")
            parent_chunks_models, child_chunks_models = split_parent_child_chunks_from_markdown(
                normalized_new_content,
                file_name=file_name,
                file_id=existing_file_id,
                parent_max_words=500,
                child_max_words=80,
                min_child_words=20,
            )

            if existing_file_id:
                for chunk in parent_chunks_models:
                    if isinstance(chunk.file_metadata, dict):
                        chunk.file_metadata["file_id"] = existing_file_id
                for chunk in child_chunks_models:
                    if isinstance(chunk.file_metadata, dict):
                        chunk.file_metadata["file_id"] = existing_file_id

            if not parent_chunks_models:
                raise ValueError("New content produced no chunks — content may be empty.")

            print("  → Step 4: Polishing child chunks...")
            child_chunks_dicts = [chunk.model_dump(by_alias=False) for chunk in child_chunks_models]
            polished_child_chunks = polish_chunks(child_chunks_dicts)

            parent_chunks_dicts = [chunk.model_dump(by_alias=True) for chunk in parent_chunks_models]

            print("  → Step 5: Storing new chunks in database...")
            await upsert_documents(parent_chunks=parent_chunks_dicts, child_chunks=polished_child_chunks)

            new_parent_id = parent_chunks_dicts[0]["parent_chunk_id"]
            print(f"✅ Document {file_name} updated successfully!")
            return {
                "id": new_parent_id,
                "parentId": new_parent_id,
                "previousParentId": parent_id,
                "fileName": file_name,
                "content": normalized_new_content,
                "size": len(normalized_new_content),
                "chunks": len(child_chunks_dicts),
            }

        except Exception as e:
            print(f"❌ Failed to update document {parent_id}: {e}")
            traceback.print_exc()
            raise RuntimeError(f"Document update failed: {str(e)}")

    @staticmethod
    async def update_file(file_id: str, new_content: str, file_name: str) -> dict:
        """
        Update all parent chunks for a fileId, then re-chunk and re-ingest.

        **Critical fix**: Preserve the existing file_id (do NOT allow re-chunk to generate a new one),
        otherwise the same logical file may split into old/new fileId groups and duplicate in sidebar.
        """
        print(f"📝 Updating full file, file_id: {file_id} ({file_name})...")

        try:
            normalized_new_content = normalize_markdown_for_modification(new_content)
            parent_collection = PARENT_STORE.collection

            def _load_existing_file_state() -> tuple[list[str], str]:
                cursor = parent_collection.find({"value.metadata.file_metadata.file_id": file_id})
                sortable_rows: list[dict[str, Any]] = []
                for row in cursor:
                    if not isinstance(row, dict):
                        continue

                    fields = ReconstructionService._extract_parent_row_fields(row)
                    if not fields:
                        continue

                    parent_id = str(fields.get("parentId") or "").strip()
                    if not parent_id:
                        continue

                    chunk_number = fields.get("chunkNumber")
                    sortable_rows.append(
                        {
                            "parentId": parent_id,
                            "chunkNumber": int(chunk_number) if isinstance(chunk_number, int) else 10**9,
                            "content": str(fields.get("content") or ""),
                        }
                    )

                sortable_rows.sort(key=lambda item: (item["chunkNumber"], item["parentId"]))
                parent_ids = [item["parentId"] for item in sortable_rows]
                merged_content = normalize_markdown_for_modification(
                    "\n\n".join(item["content"] for item in sortable_rows)
                )
                return parent_ids, merged_content

            parent_ids, existing_content = await asyncio.to_thread(_load_existing_file_state)

            if not parent_ids:
                raise RuntimeError(f"No parent chunks found for file_id={file_id}")

            if normalized_new_content == existing_content:
                print(f"ℹ️ No changes detected for file_id={file_id}; skipping delete/re-ingest.")
                return {
                    "fileId": file_id,
                    "previousFileId": file_id,
                    "fileName": file_name,
                    "content": existing_content,
                    "size": len(existing_content),
                    "parentChunks": len(parent_ids),
                    "chunks": 0,
                }

            # 1) Delete all old children + parents for this fileId
            for parent_id in parent_ids:
                await delete_children_by_parent_id(parent_id)
                await delete_parent_document(parent_id)

            # 2) Re-chunk full edited content
            print("  → Chunking new content...")
            parent_chunks_models, child_chunks_models = split_parent_child_chunks_from_markdown(
                normalized_new_content,
                file_name=file_name,
                file_id=file_id,
                parent_max_words=500,
                child_max_words=80,
                min_child_words=20,
            )

            if not parent_chunks_models:
                raise ValueError("New content produced no chunks — content may be empty.")

            # 2.5) FORCE file_id to remain the same across all new chunks
            for chunk in parent_chunks_models:
                if isinstance(chunk.file_metadata, dict):
                    chunk.file_metadata["file_id"] = file_id
            for chunk in child_chunks_models:
                if isinstance(chunk.file_metadata, dict):
                    chunk.file_metadata["file_id"] = file_id

            # 3) Polish child chunks
            print("  → Polishing child chunks...")
            child_chunks_dicts = [chunk.model_dump(by_alias=False) for chunk in child_chunks_models]
            polished_child_chunks = polish_chunks(child_chunks_dicts)

            # 4) Persist parent + child chunks
            print("  → Storing new chunks in database...")
            parent_chunks_dicts = [chunk.model_dump(by_alias=True) for chunk in parent_chunks_models]
            await upsert_documents(parent_chunks=parent_chunks_dicts, child_chunks=polished_child_chunks)

            print(f"✅ File {file_name} updated successfully!")
            return {
                "fileId": file_id,  # stays stable now
                "previousFileId": file_id,
                "fileName": file_name,
                "content": normalized_new_content,
                "size": len(normalized_new_content),
                "parentChunks": len(parent_chunks_dicts),
                "chunks": len(child_chunks_dicts),
            }

        except Exception as e:
            print(f"❌ Failed to update file {file_id}: {e}")
            traceback.print_exc()
            raise RuntimeError(f"File update failed: {str(e)}")

    @staticmethod
    async def _load_sortable_rows_for_file(file_id: str, file_name: str) -> list[dict[str, Any]]:
        """Load parent rows for a file and normalize into a deterministic sortable list."""

        def _load_file_rows() -> list[dict]:
            collection = PARENT_STORE.collection
            rows = collection.find({"value.metadata.file_metadata.file_id": file_id})
            result: list[dict] = []
            for row in rows:
                if isinstance(row, dict):
                    result.append(row)
            return result

        raw_rows = await asyncio.to_thread(_load_file_rows)
        if not raw_rows:
            raise FileNotFoundError(f"No parent chunks found for file_id={file_id}")

        sortable_rows: list[dict[str, Any]] = []
        for row in raw_rows:
            fields = ReconstructionService._extract_parent_row_fields(row)
            if not fields:
                continue

            parent_id = str(fields.get("parentId") or "").strip()
            if not parent_id:
                continue

            row_file_name = str(fields.get("fileName") or "Unknown")
            if row_file_name != file_name:
                raise RuntimeError(
                    f"file ID '{file_id}' belongs to '{row_file_name}', not '{file_name}'"
                )

            chunk_number = fields.get("chunkNumber")
            content = str(fields.get("content") or "")
            sortable_rows.append(
                {
                    "parentId": parent_id,
                    "chunkNumber": int(chunk_number) if isinstance(chunk_number, int) else 10**9,
                    "content": content,
                    "normalizedContent": normalize_markdown_for_modification(content),
                    "row": row,
                }
            )

        sortable_rows.sort(key=lambda item: (item["chunkNumber"], item["parentId"]))
        if not sortable_rows:
            raise RuntimeError(f"No usable parent chunks found for file_id={file_id}")
        return sortable_rows

    @staticmethod
    def _build_deterministic_sequence_keys(contents: list[str]) -> list[str]:
        """Build stable keys that keep duplicate-content ordering deterministic."""
        occurrences: dict[str, int] = {}
        keys: list[str] = []
        for content in contents:
            rank = occurrences.get(content, 0) + 1
            occurrences[content] = rank
            keys.append(f"{content}\u241f{rank}")
        return keys

    @staticmethod
    async def _update_parent_chunks_batch_fast(
        *,
        file_id: str,
        file_name: str,
        updates: list[dict[str, str]],
    ) -> dict[str, Any]:
        if not updates:
            return {
                "fileId": file_id,
                "fileName": file_name,
                "updatedCount": 0,
                "results": [],
                "requiresReload": False,
            }

        # 1) Deduplicate and validate incoming chunk updates.
        deduped_updates: dict[str, str] = {}
        for item in updates:
            parent_id = str(item.get("parentId") or "").strip()
            content = normalize_markdown_for_modification(str(item.get("content") or ""))
            if not parent_id:
                raise ValueError("Each update item must include a non-empty parentId")
            if not content.strip():
                raise ValueError(f"content must not be empty for parent_id={parent_id}")
            deduped_updates[parent_id] = content

        # 2) Load current state and validate parent IDs.
        sortable_rows = await ReconstructionService._load_sortable_rows_for_file(file_id, file_name)
        parent_index = {item["parentId"]: index for index, item in enumerate(sortable_rows)}
        unknown_parent_ids = [
            parent_id for parent_id in deduped_updates.keys() if parent_id not in parent_index
        ]
        if unknown_parent_ids:
            raise FileNotFoundError(
                f"Unknown parent IDs for file_id={file_id}: {', '.join(unknown_parent_ids)}"
            )

        # 3) Detect changed parents only.
        changed_parent_ids = [
            parent_id
            for parent_id, next_content in deduped_updates.items()
            if next_content != str(sortable_rows[parent_index[parent_id]]["normalizedContent"])
        ]
        if not changed_parent_ids:
            return {
                "fileId": file_id,
                "fileName": file_name,
                "updatedCount": 0,
                "results": [],
                "requiresReload": False,
            }

        # 4) Re-chunk only changed parent chunks.
        replacements_by_parent: dict[str, dict[str, Any]] = {}
        for parent_id in changed_parent_ids:
            replacement_parent_models, replacement_child_models = split_parent_child_chunks_from_markdown(
                deduped_updates[parent_id],
                file_name=file_name,
                file_id=file_id,
                parent_max_words=500,
                child_max_words=80,
                min_child_words=20,
            )
            if not replacement_parent_models:
                raise ValueError(f"content produced no chunks for parent_id={parent_id}")

            for parent_chunk in replacement_parent_models:
                if isinstance(parent_chunk.file_metadata, dict):
                    parent_chunk.file_metadata["file_id"] = file_id
            for child_chunk in replacement_child_models:
                if isinstance(child_chunk.file_metadata, dict):
                    child_chunk.file_metadata["file_id"] = file_id

            replacements_by_parent[parent_id] = {
                "parents": replacement_parent_models,
                "children": replacement_child_models,
            }

        # 5) Build post-update sequence and update chunk numbers.
        next_sequence: list[dict[str, Any]] = []
        for item in sortable_rows:
            parent_id = item["parentId"]
            replacement = replacements_by_parent.get(parent_id)
            if replacement is None:
                next_sequence.append({"type": "existing", "item": item})
                continue
            for model in replacement["parents"]:
                next_sequence.append({"type": "replacement", "parentId": parent_id, "model": model})

        for index, entry in enumerate(next_sequence):
            target_chunk_number = index
            if entry["type"] == "existing":
                row = entry["item"]["row"]
                parent_doc = row.get("value") or {}
                metadata = parent_doc.get("metadata") if isinstance(parent_doc, dict) else {}
                if not isinstance(metadata, dict):
                    metadata = {}
                parent_meta = metadata.get("parent_chunk_metadata")
                if not isinstance(parent_meta, dict):
                    parent_meta = {}
                parent_meta["parent_chunk_number"] = target_chunk_number
                metadata["parent_chunk_metadata"] = parent_meta
                parent_doc["metadata"] = metadata
                row["value"] = parent_doc
            else:
                model = entry["model"]
                if isinstance(model.parent_chunk_metadata, dict):
                    model.parent_chunk_metadata["parent_chunk_number"] = target_chunk_number

        # 6) Replace only changed parent rows.
        for parent_id in changed_parent_ids:
            await delete_children_by_parent_id(parent_id)
            await delete_parent_document(parent_id)

        child_chunks_dicts: list[dict[str, Any]] = []
        for parent_id in changed_parent_ids:
            replacement = replacements_by_parent[parent_id]
            child_chunks_dicts.extend([model.model_dump(by_alias=False) for model in replacement["children"]])
        polished_child_chunks = polish_chunks(child_chunks_dicts)

        replacement_parent_chunks_dicts: list[dict[str, Any]] = []
        for entry in next_sequence:
            if entry["type"] != "replacement":
                continue
            replacement_parent_chunks_dicts.append(entry["model"].model_dump(by_alias=True))

        if replacement_parent_chunks_dicts or polished_child_chunks:
            await upsert_documents(
                parent_chunks=replacement_parent_chunks_dicts,
                child_chunks=polished_child_chunks,
            )

        existing_parent_pairs: list[tuple[str, dict]] = []
        for entry in next_sequence:
            if entry["type"] != "existing":
                continue
            row = entry["item"]["row"]
            parent_id = str(row.get("_id") or "").strip()
            parent_value = row.get("value")
            if not parent_id or not isinstance(parent_value, dict):
                continue
            existing_parent_pairs.append((parent_id, parent_value))
        if existing_parent_pairs:
            await PARENT_STORE.amset(existing_parent_pairs)

        results: list[dict[str, Any]] = []
        for parent_id in changed_parent_ids:
            replacement = replacements_by_parent[parent_id]
            first_parent = replacement["parents"][0].model_dump(by_alias=True)
            results.append(
                {
                    "parentId": first_parent["parent_chunk_id"],
                    "previousParentId": parent_id,
                    "fileName": file_name,
                    "content": deduped_updates[parent_id],
                    "size": len(deduped_updates[parent_id]),
                    "chunks": len(replacement["children"]),
                }
            )

        return {
            "fileId": file_id,
            "fileName": file_name,
            "updatedCount": len(results),
            "results": results,
            "requiresReload": False,
        }

    @staticmethod
    async def _update_parent_chunks_batch_boundary(
        *,
        file_id: str,
        file_name: str,
        full_content: str,
        touched_parent_ids: list[str],
    ) -> dict[str, Any]:
        normalized_full_content = normalize_markdown_for_modification(full_content)
        if not normalized_full_content.strip():
            raise ValueError("fullContent must not be empty")

        sortable_rows = await ReconstructionService._load_sortable_rows_for_file(file_id, file_name)
        existing_parent_ids = {str(item["parentId"]) for item in sortable_rows}
        cleaned_touched_parent_ids = [str(parent_id).strip() for parent_id in touched_parent_ids if str(parent_id).strip()]
        if not cleaned_touched_parent_ids:
            raise ValueError("touchedParentIds must contain at least one parentId")
        unknown_touched_ids = [parent_id for parent_id in cleaned_touched_parent_ids if parent_id not in existing_parent_ids]
        if unknown_touched_ids:
            raise FileNotFoundError(
                f"Unknown touched parent IDs for file_id={file_id}: {', '.join(unknown_touched_ids)}"
            )

        existing_content = normalize_markdown_for_modification("\n\n".join(str(item["content"]) for item in sortable_rows))
        if existing_content == normalized_full_content:
            return {
                "fileId": file_id,
                "fileName": file_name,
                "updatedCount": 0,
                "results": [],
                "requiresReload": True,
            }

        # 1) Build the canonical post-edit chunk sequence from full content.
        new_parent_models, new_child_models = split_parent_child_chunks_from_markdown(
            normalized_full_content,
            file_name=file_name,
            file_id=file_id,
            parent_max_words=500,
            child_max_words=80,
            min_child_words=20,
        )
        if not new_parent_models:
            raise ValueError("fullContent produced no parent chunks")

        for parent_chunk in new_parent_models:
            if isinstance(parent_chunk.file_metadata, dict):
                parent_chunk.file_metadata["file_id"] = file_id
        for child_chunk in new_child_models:
            if isinstance(child_chunk.file_metadata, dict):
                child_chunk.file_metadata["file_id"] = file_id

        child_models_by_parent: dict[str, list[Any]] = {}
        for child_model in new_child_models:
            parent_id = str((child_model.child_chunk_metadata or {}).get("parent_id") or "").strip()
            if not parent_id:
                continue
            child_models_by_parent.setdefault(parent_id, []).append(child_model)

        old_norm_contents = [str(item["normalizedContent"]) for item in sortable_rows]
        new_norm_contents = [
            normalize_markdown_for_modification(str(model.content or ""))
            for model in new_parent_models
        ]
        old_keys = ReconstructionService._build_deterministic_sequence_keys(old_norm_contents)
        new_keys = ReconstructionService._build_deterministic_sequence_keys(new_norm_contents)

        matcher = difflib.SequenceMatcher(a=old_keys, b=new_keys, autojunk=False)
        preserved_old_to_new: dict[int, int] = {}
        old_changed_indices: set[int] = set()
        new_changed_indices: set[int] = set()
        mapping_pairs: list[tuple[int, int]] = []

        for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
            if tag == "equal":
                for old_index, new_index in zip(range(old_start, old_end), range(new_start, new_end)):
                    preserved_old_to_new[old_index] = new_index
                continue

            old_changed_indices.update(range(old_start, old_end))
            new_changed_indices.update(range(new_start, new_end))
            if tag == "replace":
                pair_count = min(old_end - old_start, new_end - new_start)
                for offset in range(pair_count):
                    mapping_pairs.append((old_start + offset, new_start + offset))

        # 2) Delete only changed/removed old parents.
        deleted_parent_ids: list[str] = []
        for old_index in sorted(old_changed_indices):
            old_parent_id = str(sortable_rows[old_index]["parentId"])
            await delete_children_by_parent_id(old_parent_id)
            await delete_parent_document(old_parent_id)
            deleted_parent_ids.append(old_parent_id)

        # 3) Prepare upserts for inserted/replaced new parents only.
        parent_dicts_to_upsert: list[dict[str, Any]] = []
        child_dicts_to_upsert: list[dict[str, Any]] = []
        new_parent_ids_by_index: dict[int, str] = {}
        for new_index in sorted(new_changed_indices):
            model = new_parent_models[new_index]
            if isinstance(model.parent_chunk_metadata, dict):
                model.parent_chunk_metadata["parent_chunk_number"] = new_index
            parent_payload = model.model_dump(by_alias=True)
            parent_id = str(parent_payload.get("parent_chunk_id") or "").strip()
            if not parent_id:
                continue
            new_parent_ids_by_index[new_index] = parent_id
            parent_dicts_to_upsert.append(parent_payload)

            children_for_parent = child_models_by_parent.get(parent_id, [])
            for child_model in children_for_parent:
                child_dicts_to_upsert.append(child_model.model_dump(by_alias=False))

        polished_children = polish_chunks(child_dicts_to_upsert) if child_dicts_to_upsert else []
        if parent_dicts_to_upsert or polished_children:
            await upsert_documents(parent_chunks=parent_dicts_to_upsert, child_chunks=polished_children)

        # 4) Re-number preserved parents to match the new sequence.
        existing_parent_pairs: list[tuple[str, dict]] = []
        for old_index, new_index in sorted(preserved_old_to_new.items(), key=lambda pair: pair[1]):
            row = sortable_rows[old_index]["row"]
            parent_id = str(row.get("_id") or "").strip()
            parent_value = row.get("value")
            if not parent_id or not isinstance(parent_value, dict):
                continue

            metadata = parent_value.get("metadata") if isinstance(parent_value.get("metadata"), dict) else {}
            parent_meta = metadata.get("parent_chunk_metadata") if isinstance(metadata.get("parent_chunk_metadata"), dict) else {}
            parent_meta["parent_chunk_number"] = new_index
            metadata["parent_chunk_metadata"] = parent_meta
            parent_value["metadata"] = metadata
            existing_parent_pairs.append((parent_id, parent_value))

        if existing_parent_pairs:
            await PARENT_STORE.amset(existing_parent_pairs)

        # 5) Emit replacement mappings where old/new pairs exist.
        results: list[dict[str, Any]] = []
        for old_index, new_index in mapping_pairs:
            old_parent_id = str(sortable_rows[old_index]["parentId"])
            new_parent_id = new_parent_ids_by_index.get(new_index)
            if not new_parent_id:
                continue
            results.append(
                {
                    "parentId": new_parent_id,
                    "previousParentId": old_parent_id,
                    "fileName": file_name,
                    "content": str(new_parent_models[new_index].content or ""),
                    "size": len(str(new_parent_models[new_index].content or "")),
                    "chunks": len(child_models_by_parent.get(new_parent_id, [])),
                }
            )

        print(
            f"  → Boundary re-chunk complete: deleted={len(deleted_parent_ids)} "
            f"inserted_or_replaced={len(new_changed_indices)} preserved={len(preserved_old_to_new)}"
        )
        return {
            "fileId": file_id,
            "fileName": file_name,
            "updatedCount": len(new_changed_indices),
            "results": results,
            "requiresReload": True,
        }

    @staticmethod
    async def update_parent_chunks_batch(
        file_id: str,
        file_name: str,
        updates: list[dict[str, str]],
        *,
        mode: Literal["fast_updates", "boundary_rechunk"] = "fast_updates",
        full_content: str | None = None,
        touched_parent_ids: list[str] | None = None,
    ) -> dict:
        """Batch update parent chunks for one logical file scope."""

        print(
            f"📝 Batch updating parent chunks for file_id={file_id} ({file_name}), mode={mode}..."
        )

        try:
            if mode == "fast_updates":
                return await ReconstructionService._update_parent_chunks_batch_fast(
                    file_id=file_id,
                    file_name=file_name,
                    updates=updates,
                )

            if mode == "boundary_rechunk":
                return await ReconstructionService._update_parent_chunks_batch_boundary(
                    file_id=file_id,
                    file_name=file_name,
                    full_content=str(full_content or ""),
                    touched_parent_ids=list(touched_parent_ids or []),
                )

            raise ValueError(f"Unsupported mode='{mode}'")
        except Exception as error:
            print(f"❌ Failed batch parent update for file_id={file_id}: {error}")
            traceback.print_exc()
            raise RuntimeError(f"Batch parent chunk update failed: {str(error)}")
