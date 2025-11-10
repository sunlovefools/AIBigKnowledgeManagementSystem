"""
Vector Store Service - Query Only
---------------------------------
Responsible for connecting to AstraDB and performing vector similarity search.

 Responsibilities (Your Scope):
 Read: Search for relevant document chunks by vector similarity.
"""

import os
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from astrapy import DataAPIClient

load_dotenv()

# --- Database Configuration ---
ASTRA_DB_URL = os.getenv("ASTRA_DB_URL")
ASTRA_DB_TOKEN = os.getenv("ASTRA_DB_TOKEN")

if not ASTRA_DB_URL or not ASTRA_DB_TOKEN:
    raise ValueError(
        "Missing required environment variables!\n"
        "Please set the following in backend/.env:\n"
        "  - ASTRA_DB_URL\n"
        "  - ASTRA_DB_TOKEN"
    )


class VectorStoreService:
    """
    Vector Store Service
    --------------------
    Connects to AstraDB and executes vector similarity searches.

    Notes:
    - The collection should already exist (created by the Document Ingestion team).
    - This class is READ-ONLY for query-side retrieval.
    """

    def __init__(self, collection_name: str = "document_vectors"):
        """
        Initialize a connection to the AstraDB collection.

        Args:
            collection_name: Name of the vector collection in AstraDB.
        """
        self.collection_name = collection_name
        self.client = DataAPIClient()
        self.database = self.client.get_database(ASTRA_DB_URL, token=ASTRA_DB_TOKEN)
        self.collection = self._get_collection()

        print(f"✅ VectorStoreService connected to collection '{self.collection_name}'")

    def _get_collection(self):
        """
        Retrieve an existing AstraDB collection.

        ⚠️ The collection must be created by the ingestion team.
        If it does not exist, an exception will be raised.
        """
        try:
            collection = self.database.get_collection(self.collection_name)
            print(f"📚 Using existing collection: {self.collection_name}")
            return collection
        except Exception as e:
            raise Exception(
                f"Failed to access collection '{self.collection_name}'.\n"
                f"Ensure that the Document Ingestion team has created it.\n"
                f"Error: {e}"
            )

    # ------------------------------------------------------------------
    # 1. Vector Similarity Search (Your Core Function)
    # ------------------------------------------------------------------
    def similarity_search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        user_id: Optional[str] = None,
        min_similarity: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        Perform a vector similarity search in AstraDB.

        Args:
            query_embedding: The query's embedding vector.
            top_k: Number of top results to return.
            user_id: Optional user filter (only search within a user's documents).
            min_similarity: Minimum similarity threshold (0–1).

        Returns:
            List of relevant document chunks, each containing:
            - chunk_id: Unique identifier for the chunk
            - text: Text content
            - metadata: Metadata (e.g., source file, page)
            - similarity_score: Cosine similarity value
            - user_id: Owner of the document
        """
        try:
            filter_query = {}
            if user_id:
                filter_query["user_id"] = user_id

            # AstraDB will automatically rank results by vector similarity
            results = self.collection.find(
                filter=filter_query if filter_query else None,
                sort={"$vector": query_embedding},
                limit=top_k,
                include_similarity=True,
            )

            formatted_results = []
            for doc in results:
                similarity_score = doc.get("$similarity", 0.0)

                # Filter out low-similarity results
                if similarity_score >= min_similarity:
                    formatted_results.append(
                        {
                            "chunk_id": doc.get("chunk_id"),
                            "text": doc.get("text"),
                            "metadata": doc.get("metadata", {}),
                            "similarity_score": similarity_score,
                            "user_id": doc.get("user_id"),
                        }
                    )

            print(f"✅ Found {len(formatted_results)} similar chunks (threshold: {min_similarity})")
            return formatted_results

        except Exception as e:
            print(f"❌ Similarity search failed: {e}")
            return []

    # ------------------------------------------------------------------
    # 2. Retrieve by Chunk ID
    # ------------------------------------------------------------------
    def get_chunk_by_id(self, chunk_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific chunk by its unique ID.

        Args:
            chunk_id: Unique chunk identifier.

        Returns:
            The chunk document if found, otherwise None.
        """
        try:
            result = self.collection.find_one({"chunk_id": chunk_id})
            return result
        except Exception as e:
            print(f"❌ Failed to retrieve chunk {chunk_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # 3. User Document Statistics
    # ------------------------------------------------------------------
    def get_user_document_count(self, user_id: str) -> int:
        """
        Count the total number of document chunks stored for a given user.
        """
        try:
        # astrapy new version need keyword-only parameter upper_bound
            return self.collection.count_documents({"user_id": user_id}, upper_bound=10_000_000)
        except Exception as e:
            print(f"❌ Failed to count user documents: {e}")
            return 0

    # ------------------------------------------------------------------
    # 4. List User Document Sources
    # ------------------------------------------------------------------
    def list_user_sources(self, user_id: str) -> List[str]:
        """
        List all unique document sources (e.g., filenames) uploaded by a user.

        Args:
            user_id: User identifier.

        Returns:
            A list of unique document source names.
        """
        try:
            results = self.collection.find({"user_id": user_id})
            sources = set()
            for doc in results:
                source = doc.get("metadata", {}).get("source")
                if source:
                    sources.add(source)
            return list(sources)
        except Exception as e:
            print(f"❌ Failed to list sources: {e}")
            return []
    
    # ------------------------------------------------------------------
    # 5. Debug Utilities
    # ------------------------------------------------------------------
    def debug_list_collections(self):
        """
        Debug helper: list all collections in the current AstraDB database.
        Compatible with new AstraPy versions that return CollectionDescriptor objects.
        """
        try:
            cols = self.database.list_collections()
            names = []
            for c in cols:
                
                if hasattr(c, "name"):
                    names.append(c.name)
                elif isinstance(c, dict) and "name" in c:
                    names.append(c["name"])
            print(f"📋 Collections in DB: {names}")
            return names
        except Exception as e:
            print(f"❌ Failed to list collections: {e}")
            return []

# ------------------------------------------------------------------
# Global Singleton Instance
# ------------------------------------------------------------------
_vector_store = None


def get_vector_store() -> VectorStoreService:
    """Get or create a single global VectorStoreService instance."""
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStoreService()
    return _vector_store
