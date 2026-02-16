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
    async def get_all_documents() -> list[dict]:
        """
        Retrieves all unique documents that have been ingested into the system.
        
        This function:
        1. Scans the Vector Store to find all unique documents
        2. For each document, retrieves the full content from the Parent Store
        3. Returns a list of document metadata with their reconstructed content

        Returns:
            list[dict]: A list of document dictionaries, each containing:
                - id: str - The parent ID (unique identifier)
                - fileName: str - Original file name
                - content: str - Full reconstructed document content
                - size: int - Character count
                - chunks: int - Number of chunks
        """
        
        print("🔄 Retrieving all documents from Vector Store...")
        
        try:
            # 1. Verify Vector Store is available
            print("  ✓ Vector Store ready")
            
            # 2. Search with a generic query to retrieve documents
            # Using a broad search term that should match most documents
            # Note: AstraDB has a hard limit of 1000 results for vector search
            # This means we can retrieve at most 1000 child chunks in one query
            # If you have more than 1000 chunks, only the most relevant will be returned
            print("  → Searching for documents (max 1000 chunks due to DB limit)...")
            try:
                # AstraDB limit is 1000 for vector search - this is a hard database constraint
                # Note: asimilarity_search returns List[Document], not List[Tuple[Document, float]]
                all_child_chunks = await VECTOR_STORE.asimilarity_search("document", k=1000)
            except Exception as search_error:
                print(f"  ⚠️  Vector search failed: {search_error}")
                print("  → Attempting fallback: using different search term...")
                # Fallback: try with different search term
                try:
                    all_child_chunks = await VECTOR_STORE.asimilarity_search("the", k=1000)
                except Exception as fallback_error:
                    print(f"  ❌ Fallback also failed: {fallback_error}")
                    raise RuntimeError(f"Vector search failed: {str(search_error)}")
            
            if not all_child_chunks:
                print("ℹ️ No documents found in the system.")
                return []
            
            print(f"📚 Found {len(all_child_chunks)} child chunks")
            
            # Warn if we hit the database limit
            if len(all_child_chunks) >= 1000:
                print("  ⚠️  WARNING: Retrieved exactly 1000 chunks (database limit)")
                print("  ⚠️  There may be more documents that were not retrieved")
                print("  ⚠️  Consider implementing pagination or more specific queries")
            
            # 3. Extract unique parent IDs and their metadata
            documents_map = {}  # Key: parent_id, Value: {file_name}
            
            for doc in all_child_chunks:
                parent_id = doc.metadata.get("parent_id")
                file_name = doc.metadata.get("document_name", "Unknown")
                
                if parent_id and parent_id not in documents_map:
                    documents_map[parent_id] = {
                        "parent_id": parent_id,
                        "file_name": file_name,
                    }
            
            print(f"🔗 Found {len(documents_map)} unique documents")
            
            if not documents_map:
                print("ℹ️ No unique documents found after deduplication.")
                return []
            
            # 4. Retrieve full parent document content
            parent_ids = list(documents_map.keys())
            print(f"  → Retrieving {len(parent_ids)} parent documents from store...")
            
            # AstraDB $in operator限制：每批最多100个ID
            BATCH_SIZE = 100
            parent_documents_dict = []
            
            try:
                for i in range(0, len(parent_ids), BATCH_SIZE):
                    batch = parent_ids[i:i+BATCH_SIZE]
                    print(f"    📦 Fetching batch {i//BATCH_SIZE + 1}: {len(batch)} documents")
                    batch_result = await PARENT_STORE.amget(batch)
                    parent_documents_dict.extend(batch_result)
                
                print(f"  ✅ Retrieved {len(parent_documents_dict)} parent documents")
            except Exception as parent_error:
                print(f"  ❌ Parent Store retrieval failed: {parent_error}")
                traceback.print_exc()
                raise RuntimeError(f"Parent document retrieval failed: {str(parent_error)}")
            
            # 5. Build response
            documents_list = []
            for idx, parent_dict in enumerate(parent_documents_dict):
                try:
                    if parent_dict:
                        # Handle both dict and Document object formats
                        if isinstance(parent_dict, dict):
                            content = parent_dict.get("page_content", "")
                        else:
                            # If it's a Document object
                            content = getattr(parent_dict, "page_content", "")
                        
                        if content:
                            parent_id = parent_ids[idx]
                            doc_info = documents_map[parent_id]
                            
                            documents_list.append({
                                "id": parent_id,
                                "fileName": doc_info["file_name"],
                                "content": content,
                                "size": len(content),
                                "chunks": sum(
                                    1 for doc in all_child_chunks 
                                    if doc.metadata.get("parent_id") == parent_id
                                ),
                            })
                except Exception as item_error:
                    print(f"  ⚠️  Error processing document at index {idx}: {item_error}")
                    continue
            
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
