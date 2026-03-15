"""
Module for handling file reconstruction and modification operations.
Provides functionality to retrieve all uploaded documents and reconstruct them
from their stored chunks via the Parent-Child RAG pattern.
"""

import asyncio
import traceback
from typing import Any

from app.vectordb.vectordb import (
    PARENT_STORE,
    delete_children_by_file_id,
    delete_children_by_parent_id,
    delete_parent_documents_by_file_id,
    delete_parent_document,
    upsert_documents,
)
from app.service.rag.ingestion.chunker import split_parent_child_chunks
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

        return {
            "parentId": parent_id,
            "fileId": file_id,
            "fileName": str(file_name),
            "content": content,
            "chunkNumber": chunk_number_int,
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
                }
            )

        # Deterministic ordering: (chunkNumber, parentId)
        sortable.sort(key=lambda item: (item["chunkNumber"], item["parentId"]))

        page_rows = sortable[: max(limit, 1)]
        has_more = len(sortable) > len(page_rows)
        next_cursor = str(page_rows[-1]["chunkNumber"]) if has_more and page_rows else None

        chunks = [
            {"parentId": r["parentId"], "content": r["content"], "size": r["size"]}
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
            parent_chunks_models, child_chunks_models = split_parent_child_chunks(
                new_content,
                file_name=file_name,
                parent_target_chars=1500,
                child_max_chars=600,
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
                "content": new_content,
                "size": len(new_content),
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
            parent_collection = PARENT_STORE.collection

            def _find_parent_ids_for_file() -> list[str]:
                cursor = parent_collection.find({"value.metadata.file_metadata.file_id": file_id})
                parent_ids: list[str] = []
                for row in cursor:
                    if isinstance(row, dict):
                        parent_id = str(row.get("_id", "")).strip()
                        if parent_id:
                            parent_ids.append(parent_id)
                return parent_ids

            parent_ids = await asyncio.to_thread(_find_parent_ids_for_file)

            if not parent_ids:
                raise RuntimeError(f"No parent chunks found for file_id={file_id}")

            # 1) Delete all old children + parents for this fileId
            for parent_id in parent_ids:
                await delete_children_by_parent_id(parent_id)
                await delete_parent_document(parent_id)

            # 2) Re-chunk full edited content
            print("  → Chunking new content...")
            parent_chunks_models, child_chunks_models = split_parent_child_chunks(
                new_content,
                file_name=file_name,
                parent_target_chars=1500,
                child_max_chars=600,
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
                "content": new_content,
                "size": len(new_content),
                "parentChunks": len(parent_chunks_dicts),
                "chunks": len(child_chunks_dicts),
            }

        except Exception as e:
            print(f"❌ Failed to update file {file_id}: {e}")
            traceback.print_exc()
            raise RuntimeError(f"File update failed: {str(e)}")
