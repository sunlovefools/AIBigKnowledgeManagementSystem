"""
Module for handling file reconstruction and modification operations.
Provides functionality to retrieve all uploaded documents and reconstruct them
from their stored chunks via the Parent-Child RAG pattern.
"""

import traceback
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from app.vectordb.vectordb import (
    PARENT_STORE,
    delete_children_by_parent_id, delete_parent_document, upsert_documents
)
from app.service.rag.ingestion.chunker import split_parent_child_chunks
from app.service.rag.ingestion.chunk_polisher import polish_chunks


_VECTOR_DB_PARENT_CHUNKS_LOG_FILE = "vector_database_parent_chunks_log.txt"


def _resolve_vector_db_log_path() -> Path:
    """Resolve a consistent log file path for logging vector database operations."""
    cwd = Path.cwd()
    if (cwd / "backend").is_dir():
        backend_dir = cwd / "backend"
    elif cwd.name == "backend":
        backend_dir = cwd
    else:
        backend_dir = Path(__file__).resolve().parents[2]

    logs_dir = backend_dir / "debug" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir / _VECTOR_DB_PARENT_CHUNKS_LOG_FILE


def _log_vector_db_result(function_name: str, retrieved: dict | list, context: dict | None = None) -> None:
    """Log the results of vector DB retrieval operations for debugging and auditing."""
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        lines = [
            "=" * 80,
            f"Function: {function_name}",
            f"Timestamp: {timestamp}",
        ]

        if context:
            lines.append("Context:")
            lines.append(json.dumps(context, ensure_ascii=False, indent=2))

        lines.append("Retrieved:")
        lines.append(json.dumps(retrieved, ensure_ascii=False, indent=2))

        log_path = _resolve_vector_db_log_path()
        with log_path.open("a", encoding="utf-8") as file_handle:
            file_handle.write("\n".join(lines) + "\n")
    except Exception as log_error:
        print(f"⚠️ Failed to write vector DB debug log: {log_error}")


class ReconstructionService:
    """A wrapper service for reconstructing files from stored chunks."""

    @staticmethod
    async def _find_parent_chunks_in_range(file_id: str, current_chunk_number: int, limit: int) -> tuple[list[dict], bool, str | None]:
        """Find parent chunks by file_id and parent_chunk_number range using collection.find."""

        def _query_rows() -> list[dict]:
            # Access the raw Astra collection for direct metadata filtering.
            collection = PARENT_STORE.collection

            # Fetch only rows for this file and only chunk numbers within the current cursor window:
            # current_chunk_number < x < current_chunk_number + limit
            # This keeps pagination deterministic and bounded per request.
            cursor = collection.find(
                {
                    "value.metadata.file_metadata.file_id": file_id,
                    "value.metadata.parent_chunk_metadata.parent_chunk_number": {
                        "$gt": current_chunk_number,
                        "$lt": current_chunk_number + limit,
                    },
                }
            )

            # Materialize cursor results into a plain list for async handoff.
            rows: list[dict] = []
            for row in cursor:
                if isinstance(row, dict):
                    rows.append(row)
            return rows

        # Run blocking DB iteration off the event loop thread.
        rows = await asyncio.to_thread(_query_rows)

        # Build an internal sortable structure that retains chunkNumber.
        # chunkNumber is required for ordering and next cursor computation,
        # while the public response only returns parentId/content/size.
        sorted_rows: list[dict] = []
        for row in rows:
            parent_id = str(row.get("_id", ""))
            value = row.get("value")
            if not isinstance(value, dict):
                continue

            metadata = value.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}

            parent_chunk_metadata = metadata.get("parent_chunk_metadata") or {}
            chunk_number_raw = parent_chunk_metadata.get("parent_chunk_number")
            if not isinstance(chunk_number_raw, (int, float)):
                continue

            content = str(value.get("page_content", ""))
            sorted_rows.append(
                {
                    "chunkNumber": int(chunk_number_raw),
                    "parentId": parent_id,
                    "content": content,
                    "size": len(content),
                }
            )

        # Stable ordering guarantees predictable pagination and cursor progression.
        sorted_rows.sort(key=lambda item: (item["chunkNumber"], item["parentId"]))

        # Keep the current strict-range behavior where at most (limit - 1) items are returned.
        window_size = max(limit - 1, 1)
        page_rows = sorted_rows[:window_size]

        # If the current page is full, caller can continue by using next_cursor.
        has_more = len(page_rows) == window_size
        # Cursor uses the last emitted chunk number to continue from that position.
        next_cursor = str(page_rows[-1]["chunkNumber"]) if has_more and page_rows else None

        # Public payload intentionally excludes chunkNumber.
        chunks = [
            {
                "parentId": row["parentId"],
                "content": row["content"],
                "size": row["size"],
            }
            for row in page_rows
        ]

        return chunks, has_more, next_cursor

    @staticmethod
    def _sorted_parent_ids_for_file(all_child_chunks, file_name: str) -> list[str]:
        """Get deterministic ordered parent IDs for a file name."""
        parent_first_chunk: dict[str, int] = {}

        for child_doc in all_child_chunks:
            metadata = child_doc.metadata or {}
            file_metadata = metadata.get("file_metadata") or {}
            child_chunk_metadata = metadata.get("child_chunk_metadata") or {}

            if file_metadata.get("file_name", "Unknown") != file_name:
                continue

            parent_id = child_chunk_metadata.get("parent_id")
            if not parent_id:
                continue

            chunk_number = child_chunk_metadata.get("child_chunk_number")
            if isinstance(chunk_number, (int, float)):
                chunk_number = int(chunk_number)
            else:
                chunk_number = 10**9

            existing = parent_first_chunk.get(parent_id)
            if existing is None or chunk_number < existing:
                parent_first_chunk[parent_id] = chunk_number

        return sorted(parent_first_chunk.keys(), key=lambda pid: (parent_first_chunk[pid], pid))
    
    @staticmethod
    async def get_all_preview_files() -> list[dict]:
        """Retrieve merged file list with one parent-chunk preview per filename."""
        print("🔄 Retrieving filename-merged summaries from Parent Store...")

        try:
            rows = await PARENT_STORE.get_all_files()
            if not rows:
                return []

            summaries: list[dict] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue

                # The value will have everything of the chunk, including the metadata, filename and content.
                parent_doc = row.get("value")
                if not isinstance(parent_doc, dict):
                    continue

                metadata = parent_doc.get("metadata", {}) or {}
                file_metadata = metadata.get("file_metadata", {}) or {}
                file_name = file_metadata.get("file_name") or metadata.get("source") or "Unknown"
                file_id = file_metadata.get("file_id") or ""
                preview_text = str(parent_doc.get("page_content", ""))

                # Append a preview of the first parent chunk for each file.
                summaries.append(
                    {
                        "fileId": file_id,
                        "fileName": file_name,
                        "preview": preview_text,
                    }
                )

            sorted_summaries = sorted(summaries, key=lambda item: str(item.get("fileName", "")).lower())
            _log_vector_db_result(
                function_name="get_all_preview_files",
                context={"totalRows": len(rows), "returnedSummaries": len(sorted_summaries)},
                retrieved=sorted_summaries,
            )
            return sorted_summaries
        except RuntimeError:
            raise
        except Exception as error:
            print(f"❌ Failed to retrieve file summaries: {error}")
            traceback.print_exc()
            raise RuntimeError(f"File summary retrieval failed: {str(error)}")

    @staticmethod
    async def get_file_parent_chunks(file_id: str, limit: int, cursor: str | None) -> dict:
        """Retrieve paginated parent chunks for a merged file ID item."""
        print(f"🔄 Retrieving paginated parent chunks for file_id: {file_id}")

        try:
            # Cursor is the previously returned chunkNumber; default -1 starts from the beginning.
            current_chunk_number = -1
            if cursor:
                try:
                    current_chunk_number = int(cursor)
                except ValueError:
                    # Invalid cursor falls back to first page behavior.
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
            # Persist exact output and retrieval context for debugging/auditing.
            _log_vector_db_result(
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

    # @staticmethod
    # async def get_all_documents() -> list[dict]:
    #     """
    #     Retrieves all unique documents that have been ingested into the system.

    #     This function iterates directly through the parent document store keys
    #     and fetches each parent document by key.

    #     Returns:
    #         list[dict]: A list of document dictionaries, each containing:
    #             - id: str - The parent ID (unique identifier)
    #             - fileName: str - Original file name
    #             - content: str - Full reconstructed document content
    #             - size: int - Character count
    #             - chunks: int - Number of chunks (0 when unavailable)
    #     """

    #     print("🔄 Retrieving all documents from Parent Store keys...")

    #     try:
    #         documents_list: list[dict] = []
    #         count = 0

    #         async for parent_id in PARENT_STORE.ayield_keys():
    #             try:
    #                 parent_doc = await PARENT_STORE.aget(parent_id)
    #                 if not parent_doc:
    #                     continue

    #                 if isinstance(parent_doc, dict):
    #                     content = str(parent_doc.get("page_content", ""))
    #                     metadata = parent_doc.get("metadata", {}) or {}
    #                 else:
    #                     content = str(getattr(parent_doc, "page_content", ""))
    #                     metadata = getattr(parent_doc, "metadata", {}) or {}

    #                 if not content:
    #                     continue

    #                 file_name = (
    #                     (metadata.get("file_metadata") or {}).get("file_name")
    #                     or metadata.get("source")
    #                     or "Unknown"
    #                 )

    #                 documents_list.append(
    #                     {
    #                         "id": parent_id,
    #                         "fileName": file_name,
    #                         "content": content,
    #                         "size": len(content),
    #                         "chunks": int(metadata.get("chunks", 0)) if str(metadata.get("chunks", "")).isdigit() else 0,
    #                     }
    #                 )
    #                 count += 1
    #             except Exception as item_error:
    #                 print(f"  ⚠️  Error processing parent key {parent_id}: {item_error}")
    #                 continue

    #         if count == 0:
    #             print("ℹ️ No documents found in the system.")
    #             return []

    #         print(f"✅ Successfully reconstructed {len(documents_list)} documents")
    #         return documents_list

    #     except RuntimeError as e:
    #         print(f"❌ Runtime Error: {e}")
    #         raise
    #     except Exception as e:
    #         print(f"❌ Unexpected error retrieved documents: {e}")
    #         traceback.print_exc()
    #         raise RuntimeError(f"Document retrieval failed: {str(e)}")

    @staticmethod
    async def get_document_by_id(parent_id: str) -> dict | None:
        """
        Retrieves a specific document by its parent ID.

        Args:
            parent_id (str): The unique parent document ID

        Returns:
            dict: Document information with content, or None if not found
        """
        
        try:
            parent_doc = await PARENT_STORE.aget(parent_id)
            
            if not parent_doc or "page_content" not in parent_doc:
                return None

            metadata = parent_doc.get("metadata", {}) or {}
            file_metadata = metadata.get("file_metadata", {}) or {}
            file_name = file_metadata.get("file_name") or metadata.get("source") or "Unknown"
            
            return {
                "id": parent_id,
                "fileName": file_name,
                "content": parent_doc["page_content"],
                "size": len(parent_doc["page_content"]),
            }
            
        except Exception as e:
            print(f"❌ Failed to retrieve document {parent_id}: {e}")
            return None

    @staticmethod
    async def update_document(parent_id: str, new_content: str, file_name: str) -> dict:
        """
        Updates a document's content by:
        1. Deleting old child chunks and parent document
        2. Re-chunking the new content
        3. Polishing and re-embedding the new chunks
        4. Storing everything back in the database

        Args:
            parent_id: The existing parent document ID to update
            new_content: The new text content for the document
            file_name: The original file name

        Returns:
            dict: Updated document info with new chunk count and size
        """
        print(f"📝 Updating document {parent_id} ({file_name})...")

        try:
            # 1. Delete old child chunks
            print("  → Step 1: Deleting old child chunks...")
            await delete_children_by_parent_id(parent_id)

            # 2. Delete old parent document
            print("  → Step 2: Deleting old parent document...")
            await delete_parent_document(parent_id)

            # 3. Re-chunk the new content
            print("  → Step 3: Re-chunking new content...")
            parent_chunks_models, child_chunks_models = split_parent_child_chunks(
                new_content,
                file_name=file_name,
                parent_target_chars=1500,
                child_max_chars=600
            )

            if not parent_chunks_models:
                raise ValueError("New content produced no chunks — content may be empty.")

            # 4. Polish child chunks
            print("  → Step 4: Polishing child chunks...")
            child_chunks_dicts = [chunk.model_dump(by_alias=False) for chunk in child_chunks_models]
            polished_child_chunks = polish_chunks(child_chunks_dicts)

            # 5. Prepare parent chunks
            parent_chunks_dicts = [chunk.model_dump(by_alias=True) for chunk in parent_chunks_models]

            # 6. Upsert new chunks into the database
            print("  → Step 5: Storing new chunks in database...")
            await upsert_documents(
                parent_chunks=parent_chunks_dicts,
                child_chunks=polished_child_chunks
            )

            print(f"✅ Document {file_name} updated successfully!")
            new_parent_id = parent_chunks_dicts[0]["parent_chunk_id"]
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
        Updates all parent chunks of one merged file by file_id, then re-chunks and re-ingests.

        Args:
            file_id: The file identifier that groups parent chunks.
            new_content: The full edited text content.
            file_name: The filename used for metadata.

        Returns:
            dict: Updated file metadata.
        """
        print(f"📝 Updating full file, file_id: {file_id} ({file_name})...")

        try:
            parent_collection = PARENT_STORE.collection

            # Search for all parent chunks with this file_id to find their parent IDs.
            def _find_parent_ids_for_file() -> list[str]:
                cursor = parent_collection.find({"value.metadata.file_metadata.file_id": file_id})
                parent_ids: list[str] = []
                for row in cursor:
                    if isinstance(row, dict):
                        parent_id = str(row.get("_id", "")).strip()
                        if parent_id:
                            parent_ids.append(parent_id)
                # Return the parent chunk IDs of all parent chunks that belong to this file_id 
                return parent_ids

            parent_ids = await asyncio.to_thread(_find_parent_ids_for_file)

            if not parent_ids:
                raise RuntimeError(f"No parent chunks found for file_id={file_id}")

            # 1. Delete all child chunks and parent chunks for this file.
            for parent_id in parent_ids:
                await delete_children_by_parent_id(parent_id)
                await delete_parent_document(parent_id)

            # 2. Re-chunk full edited content.
            print("Chunking new content")
            parent_chunks_models, child_chunks_models = split_parent_child_chunks(
                new_content,
                file_name=file_name,
                parent_target_chars=1500,
                child_max_chars=600,
            )

            if not parent_chunks_models:
                raise ValueError("New content produced no chunks — content may be empty.")

            # 3. Polish child chunks.
            print("Polishing child chunks")
            child_chunks_dicts = [chunk.model_dump(by_alias=False) for chunk in child_chunks_models]
            polished_child_chunks = polish_chunks(child_chunks_dicts)

            # 4. Persist parent + child chunks.
            print("Storing new chunks in database")
            parent_chunks_dicts = [chunk.model_dump(by_alias=True) for chunk in parent_chunks_models]
            await upsert_documents(
                parent_chunks=parent_chunks_dicts,
                child_chunks=polished_child_chunks,
            )

            first_parent = parent_chunks_dicts[0]
            resulting_file_id = (
                ((first_parent.get("file_metadata") or {}).get("file_id"))
                or file_id
            )

            print(f"✅ File {file_name} updated successfully!")
            return {
                "fileId": resulting_file_id,
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
