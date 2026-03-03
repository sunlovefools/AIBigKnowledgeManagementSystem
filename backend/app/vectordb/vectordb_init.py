import os
import asyncio
from typing import Dict, Any
from langchain_astradb import AstraDBVectorStore
from astrapy import DataAPIClient
from app.embedding.embedding_client import BeamGemmaEmbeddings
from app.embedding.local_embedding_client import LocalGemmaEmbeddings


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _build_embeddings_instance():
    provider = (os.getenv("EMBEDDING_PROVIDER", "LOCAL") or "").strip().upper()

    if provider == "BEAM":
        print("Using BEAM embedding provider.")
        return BeamGemmaEmbeddings()

    if provider == "LOCAL":
        model_name = (
            os.getenv("LOCAL_EMBEDDING_MODEL") or "google/embeddinggemma-300m"
        ).strip()
        swap_to_ram = _parse_bool_env("EMBEDDING_SWAP_TO_RAM", default=False)
        gpu_ingest_only = _parse_bool_env("EMBEDDING_GPU_INGEST_ONLY", default=True)
        print(
            "Using LOCAL embedding provider "
            f"(model={model_name}, swap_to_ram={swap_to_ram}, "
            f"gpu_ingest_only={gpu_ingest_only})."
        )
        return LocalGemmaEmbeddings(
            model_name=model_name,
            swap_to_ram=swap_to_ram,
            gpu_ingest_only=gpu_ingest_only,
        )

    raise ValueError(
        f"Invalid EMBEDDING_PROVIDER={provider!r}. Expected 'LOCAL' or 'BEAM'."
    )


# Initialize the embedding model instance
try:
    EMBEDDINGS_INSTANCE = _build_embeddings_instance()
except ValueError as error:
    print(f"Configuration Warning: {error}. Attempting database initialization.")
    EMBEDDINGS_INSTANCE = None

# Load Astra credentials from .env files
ASTRA_DB_URL = os.getenv("ASTRA_DB_URL")
ASTRA_DB_TOKEN = os.getenv("ASTRA_DB_TOKEN")

# Collection names
CHILD_COLLECTION_NAME = "Default_Child_Collection" # Child Chunks that have embeddings
PARENT_COLLECTION_NAME = "Default_Parent_Collection" # Parent Documents 

class AstraParentDocumentStore:
    """A custom wrapper around an Astra DB collection for storing Parent Documents in a key-value format."""

    def __init__(self, *, collection_name: str, api_endpoint: str, token: str) -> None:
        """
        Initializes the AstraParentDocumentStore.
        """
        client = DataAPIClient()

        # Storing the collection object for later use
        self.collection = client.get_database(api_endpoint, token=token).get_collection(collection_name)

    async def amset(self, key_value_pairs: list[tuple[str, dict]]) -> None:
        """
        Upsert multiple key-value pairs into the collection.
        """
        await asyncio.to_thread(self._mset, key_value_pairs)

    def _mset(self, key_value_pairs: list[tuple[str, dict]]) -> None:
        """
        Synchronous helper for amset to perform the upsert operations.
        """
        for key, value in key_value_pairs:
            self.collection.replace_one(
                filter={"_id": key},
                replacement={"_id": key, "value": value},
                upsert=True,
            )

    async def amget(self, keys: list[str]) -> list[dict | None]:
        """
        Retrieve multiple values by their keys. Returns a list of values corresponding to the input keys.
        """
        return await asyncio.to_thread(self._mget, keys)

    def _mget(self, keys: list[str]) -> list[dict | None]:
        """
        Synchronous helper for amget to perform the retrieval operations.
        """
        if not keys:
            return []

        values_by_id: dict[str, dict | None] = {key: None for key in keys}
        cursor = self.collection.find(
            filter={"_id": {"$in": list(keys)}},
            projection={"_id": True, "value": True},
        )
        for row in cursor:
            row_id = row.get("_id")
            if isinstance(row_id, str):
                values_by_id[row_id] = row.get("value")

        return [values_by_id.get(key) for key in keys]

    async def aget(self, key: str) -> dict | None:
        """
        Retrieve a single value by its key. Returns the value or None if not found.
        """
        return await asyncio.to_thread(self._get, key)

    def _get(self, key: str) -> dict | None:
        row = self.collection.find_one(
            filter={"_id": key},
            projection={"value": True},
        )
        if not row:
            return None
        return row.get("value")

    async def ayield_keys(self):
        keys = await asyncio.to_thread(self._list_keys)
        for key in keys:
            yield key

    async def get_all_files(self) -> list[dict]:
        """Retrieve all parent-store rows from the collection."""
        return await asyncio.to_thread(self._get_all_files)

    def _get_all_files(self) -> list[dict]:
        files: list[dict] = []
        cursor = self.collection.find(
            {"value.metadata.parent_chunk_metadata.parent_chunk_number": 0}
        )
        for row in cursor:
            if isinstance(row, dict):
                files.append(row)
        return files

    def _list_keys(self) -> list[str]:
        keys: list[str] = []
        cursor = self.collection.find(filter={}, projection={"_id": True})
        for row in cursor:
            row_id = row.get("_id")
            if isinstance(row_id, str):
                keys.append(row_id)
        return keys


def init_vector_db():
    """
    Initializes the Astra DB collections for both Parent Documents and Child Chunks,
    and returns a dictionary containing the instantiated LangChain store objects.
    """
    if not ASTRA_DB_URL or not ASTRA_DB_TOKEN:
        raise ValueError("ERROR:Missing Astra DB credentials in .env file")
        
    # Initialize a collections to hold the LangChain store objects
    collections: Dict[str, Any] = {}
    
    # 1. Initialize Vector Store (Child Chunks) using AstraDBVectorStore
    print(f"Initializing vector store collection '{CHILD_COLLECTION_NAME}' with LangChain...")
    
    try:
        if EMBEDDINGS_INSTANCE is None:
            raise RuntimeError("Embedding provider failed to initialize.")

        # Instantiating the LangChain class ensures the collection exists with vector configuration.
        vector_store = AstraDBVectorStore(
            embedding=EMBEDDINGS_INSTANCE,
            collection_name=CHILD_COLLECTION_NAME,
            token=ASTRA_DB_TOKEN,
            api_endpoint=ASTRA_DB_URL,
            autodetect_collection=True, # Reuses the exisiting collection 
            content_field="content",
        )
        collections['vector_store'] = vector_store
        print(f"✅ LangChain AstraDBVectorStore initialized for '{CHILD_COLLECTION_NAME}'.")
    except Exception as e:
        print(f"❌ Failed to initialize AstraDBVectorStore: {e}")
        raise

    # 2. Initialize Document Store (Parent Documents) using AstraDBStore
    print(f"Initializing document store collection '{PARENT_COLLECTION_NAME}' with LangChain...")
    
    try:
        parent_store = AstraParentDocumentStore(
            collection_name=PARENT_COLLECTION_NAME,
            token=ASTRA_DB_TOKEN,
            api_endpoint=ASTRA_DB_URL,
        )
        collections['parent_store'] = parent_store
        print(f"✅ Astra parent document store initialized for '{PARENT_COLLECTION_NAME}'.")
    except Exception as e:
        print(f"❌ Failed to initialize AstraDBStore: {e}")
        raise

    # Return the instantiated LangChain store objects.
    return collections
