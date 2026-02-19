"""
Module for handling file reconstruction and modification operations.
Provides functionality to retrieve all uploaded documents and reconstruct them
from their stored chunks via the Parent-Child RAG pattern.
"""

import traceback
from app.vectordb.vectordb import (
    VECTOR_STORE, PARENT_STORE,
    delete_children_by_parent_id, delete_parent_document, upsert_documents
)
from app.service.rag.ingestion.chunker import split_parent_child_chunks
from app.service.rag.ingestion.chunk_polisher import polish_chunks


class ReconstructionService:
    """Service for reconstructing files from stored chunks."""

    @staticmethod
    async def _collect_child_chunks():
        """Retrieve child chunks from vector DB with fallback search."""
        try:
            return await VECTOR_STORE.asimilarity_search("document", k=1000)
        except Exception as search_error:
            print(f"  ⚠️  Vector search failed: {search_error}")
            print("  → Attempting fallback: using different search term...")
            try:
                return await VECTOR_STORE.asimilarity_search("the", k=1000)
            except Exception as fallback_error:
                print(f"  ❌ Fallback also failed: {fallback_error}")
                raise RuntimeError(f"Vector search failed: {str(search_error)}")

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
    def _truncate_preview(text: str, preview_length: int) -> str:
        if not text:
            return ""
        normalized = " ".join(text.split())
        if len(normalized) <= preview_length:
            return normalized
        return f"{normalized[:preview_length].rstrip()}..."

    @staticmethod
    async def get_file_summaries(preview_length: int = 220) -> list[dict]:
        """Retrieve merged file list with one parent-chunk preview per filename."""
        print("🔄 Retrieving filename-merged summaries from Vector Store...")

        try:
            all_child_chunks = await ReconstructionService._collect_child_chunks()
            if not all_child_chunks:
                return []

            files_map: dict[str, list[str]] = {}

            file_names = {
                ((doc.metadata or {}).get("file_metadata") or {}).get("file_name", "Unknown")
                for doc in all_child_chunks
                if ((doc.metadata or {}).get("child_chunk_metadata") or {}).get("parent_id")
            }

            for file_name in file_names:
                ordered_parent_ids = ReconstructionService._sorted_parent_ids_for_file(all_child_chunks, file_name)
                if ordered_parent_ids:
                    files_map[file_name] = ordered_parent_ids

            if not files_map:
                return []

            summaries: list[dict] = []

            for file_name in sorted(files_map.keys(), key=lambda value: value.lower()):
                ordered_parent_ids = files_map[file_name]
                first_parent_id = ordered_parent_ids[0]
                preview_text = ""

                parent_doc = await PARENT_STORE.aget(first_parent_id)
                if isinstance(parent_doc, dict):
                    preview_text = str(parent_doc.get("page_content", ""))

                summaries.append(
                    {
                        "fileName": file_name,
                        "preview": ReconstructionService._truncate_preview(preview_text, preview_length),
                        "totalParentChunks": len(ordered_parent_ids),
                    }
                )

            return summaries
        except RuntimeError:
            raise
        except Exception as error:
            print(f"❌ Failed to retrieve file summaries: {error}")
            traceback.print_exc()
            raise RuntimeError(f"File summary retrieval failed: {str(error)}")

    @staticmethod
    async def get_file_parent_chunks(file_name: str, limit: int = 7, cursor: str | None = None) -> dict:
        """Retrieve paginated parent chunks for a merged filename item."""
        print(f"🔄 Retrieving paginated parent chunks for file: {file_name}")

        try:
            all_child_chunks = await ReconstructionService._collect_child_chunks()
            if not all_child_chunks:
                return {
                    "fileName": file_name,
                    "chunks": [],
                    "totalParentChunks": 0,
                    "hasMore": False,
                    "nextCursor": None,
                }

            ordered_parent_ids = ReconstructionService._sorted_parent_ids_for_file(all_child_chunks, file_name)
            total_parent_chunks = len(ordered_parent_ids)

            if total_parent_chunks == 0:
                return {
                    "fileName": file_name,
                    "chunks": [],
                    "totalParentChunks": 0,
                    "hasMore": False,
                    "nextCursor": None,
                }

            start_index = 0
            if cursor:
                try:
                    start_index = max(int(cursor), 0)
                except ValueError:
                    start_index = 0

            end_index = min(start_index + limit, total_parent_chunks)
            page_parent_ids = ordered_parent_ids[start_index:end_index]

            parent_docs = await PARENT_STORE.amget(page_parent_ids)
            chunks = []
            for idx, parent_doc in enumerate(parent_docs):
                if not isinstance(parent_doc, dict):
                    continue
                content = str(parent_doc.get("page_content", ""))
                chunks.append(
                    {
                        "parentId": page_parent_ids[idx],
                        "content": content,
                        "size": len(content),
                    }
                )

            has_more = end_index < total_parent_chunks
            next_cursor = str(end_index) if has_more else None

            return {
                "fileName": file_name,
                "chunks": chunks,
                "totalParentChunks": total_parent_chunks,
                "hasMore": has_more,
                "nextCursor": next_cursor,
            }
        except RuntimeError:
            raise
        except Exception as error:
            print(f"❌ Failed to retrieve parent chunks for {file_name}: {error}")
            traceback.print_exc()
            raise RuntimeError(f"File chunk retrieval failed: {str(error)}")

    @staticmethod
    async def get_all_documents() -> list[dict]:
        """
        Retrieves all unique documents that have been ingested into the system.

        This function iterates directly through the parent document store keys
        and fetches each parent document by key.

        Returns:
            list[dict]: A list of document dictionaries, each containing:
                - id: str - The parent ID (unique identifier)
                - fileName: str - Original file name
                - content: str - Full reconstructed document content
                - size: int - Character count
                - chunks: int - Number of chunks (0 when unavailable)
        """

        print("🔄 Retrieving all documents from Parent Store keys...")

        try:
            documents_list: list[dict] = []
            count = 0

            async for parent_id in PARENT_STORE.ayield_keys():
                try:
                    parent_doc = await PARENT_STORE.aget(parent_id)
                    if not parent_doc:
                        continue

                    if isinstance(parent_doc, dict):
                        content = str(parent_doc.get("page_content", ""))
                        metadata = parent_doc.get("metadata", {}) or {}
                    else:
                        content = str(getattr(parent_doc, "page_content", ""))
                        metadata = getattr(parent_doc, "metadata", {}) or {}

                    if not content:
                        continue

                    file_name = (
                        (metadata.get("file_metadata") or {}).get("file_name")
                        or metadata.get("source")
                        or "Unknown"
                    )

                    documents_list.append(
                        {
                            "id": parent_id,
                            "fileName": file_name,
                            "content": content,
                            "size": len(content),
                            "chunks": int(metadata.get("chunks", 0)) if str(metadata.get("chunks", "")).isdigit() else 0,
                        }
                    )
                    count += 1
                except Exception as item_error:
                    print(f"  ⚠️  Error processing parent key {parent_id}: {item_error}")
                    continue

            if count == 0:
                print("ℹ️ No documents found in the system.")
                return []

            print(f"✅ Successfully reconstructed {len(documents_list)} documents")
            return documents_list

        except RuntimeError as e:
            print(f"❌ Runtime Error: {e}")
            raise
        except Exception as e:
            print(f"❌ Unexpected error retrieved documents: {e}")
            traceback.print_exc()
            raise RuntimeError(f"Document retrieval failed: {str(e)}")

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
            
            return {
                "id": parent_id,
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
            return {
                "id": parent_chunks_dicts[0]["_id"],
                "fileName": file_name,
                "content": new_content,
                "size": len(new_content),
                "chunks": len(child_chunks_dicts),
            }

        except Exception as e:
            print(f"❌ Failed to update document {parent_id}: {e}")
            traceback.print_exc()
            raise RuntimeError(f"Document update failed: {str(e)}")
