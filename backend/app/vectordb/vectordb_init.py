import asyncio
import os
from typing import Any, Dict

from astrapy import DataAPIClient
from astrapy.constants import DefaultIdType, VectorMetric
from astrapy.info import (
    CollectionDefaultIDOptions,
    CollectionDefinition,
    CollectionLexicalOptions,
    CollectionVectorOptions,
)
from langchain_astradb import AstraDBVectorStore
from langchain_astradb.utils.astradb import SetupMode

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


def _parse_optional_env(name: str) -> str | None:
    """
    Read an optional env var and treat comment/placeholder values as unset.
    """
    raw = os.getenv(name)
    if raw is None:
        return None

    value = raw.strip()
    if not value:
        return None
    if value.startswith("#"):
        return None
    return value


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


def _build_local_embeddings_instance():
    """Create a LOCAL embedding client using env-backed defaults."""
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


try:
    EMBEDDINGS_INSTANCE = _build_embeddings_instance()
except ValueError as error:
    print(f"Configuration Warning: {error}. Attempting database initialization.")
    EMBEDDINGS_INSTANCE = None


ASTRA_DB_URL = os.getenv("ASTRA_DB_URL")
ASTRA_DB_TOKEN = os.getenv("ASTRA_DB_TOKEN")
# For ingestion/vector RAG data, default to Astra's standard keyspace unless overridden.
ASTRA_DB_KEYSPACE = _parse_optional_env("ASTRA_DB_KEYSPACE") or "default_keyspace"

CHILD_COLLECTION_NAME = os.getenv("CHILD_COLLECTION_NAME") or "Default_Child_Collection"
PARENT_COLLECTION_NAME = os.getenv("PARENT_COLLECTION_NAME") or "Default_Parent_Collection"


def _get_vector_database():
    """Return the configured Astra database, honoring optional keyspace."""

    client = DataAPIClient()
    return client.get_database(
        ASTRA_DB_URL,
        token=ASTRA_DB_TOKEN,
        keyspace=ASTRA_DB_KEYSPACE,
    )


def _child_collection_definition() -> CollectionDefinition:
    """Return the vector collection definition expected by the app."""

    english_lexical_options = CollectionLexicalOptions(
        enabled=True,
        analyzer={
            "tokenizer": {"name": "standard"},
            "filters": [
                {"name": "lowercase"},
                {"name": "porterstem"},
                {"name": "asciifolding"},
                {"name": "stop"},
            ],
        },
    )
    return CollectionDefinition(
        lexical=english_lexical_options,
        vector=CollectionVectorOptions(
            dimension=768,
            metric=VectorMetric.COSINE,
        ),
        indexing={
            "allow": [
                "metadata.user_id",
                "metadata.file_metadata.file_name",
                "metadata.file_metadata.file_id",
                "metadata.collection_metadata.collection_id",
                "metadata.collection_metadata.collection_name",
                "metadata.child_chunk_metadata.parent_id",
                "metadata.child_chunk_metadata.child_chunk_number",
            ]
        },
        default_id=CollectionDefaultIDOptions(default_id_type=DefaultIdType.UUIDV6),
    )


def _parent_collection_definition() -> CollectionDefinition:
    """Return the parent document collection definition expected by the app."""

    return CollectionDefinition(
        indexing={
            "allow": [
                "value.metadata.user_id",
                "value.metadata.file_metadata.file_name",
                "value.metadata.file_metadata.file_id",
                "value.metadata.collection_metadata.collection_id",
                "value.metadata.collection_metadata.collection_name",
                "value.metadata.parent_chunk_metadata.parent_chunk_number",
            ]
        },
        default_id=CollectionDefaultIDOptions(default_id_type=DefaultIdType.UUIDV6),
    )


def _ensure_collection(
    database: Any,
    name: str,
    definition: CollectionDefinition | None = None,
) -> Any:
    """Create the collection only when it does not already exist."""

    existing_names = set(database.list_collection_names())
    if name in existing_names:
        return database.get_collection(name)
    if definition is None:
        return database.create_collection(name)
    return database.create_collection(name, definition=definition)


class AstraParentDocumentStore:
    """A custom wrapper around an Astra DB collection for storing parent documents."""

    def __init__(self, *, collection_name: str, api_endpoint: str, token: str) -> None:
        _ = api_endpoint, token
        self.collection = _get_vector_database().get_collection(collection_name)

    async def amset(self, key_value_pairs: list[tuple[str, dict]]) -> None:
        await asyncio.to_thread(self._mset, key_value_pairs)

    def _mset(self, key_value_pairs: list[tuple[str, dict]]) -> None:
        for key, value in key_value_pairs:
            self.collection.replace_one(
                filter={"_id": key},
                replacement={"_id": key, "value": value},
                upsert=True,
            )

    async def amget(self, keys: list[str]) -> list[dict | None]:
        return await asyncio.to_thread(self._mget, keys)

    def _mget(self, keys: list[str]) -> list[dict | None]:
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
    Initialize Astra DB collections for both parent documents and child chunks.
    """

    if not ASTRA_DB_URL or not ASTRA_DB_TOKEN:
        raise ValueError("ERROR:Missing Astra DB credentials in .env file")

    if EMBEDDINGS_INSTANCE is None:
        raise RuntimeError("Embedding provider failed to initialize.")

    collections: Dict[str, Any] = {}
    database = _get_vector_database()

    print(f"Ensuring Astra collections exist in keyspace={ASTRA_DB_KEYSPACE or '<default>'}...")
    _ensure_collection(database, CHILD_COLLECTION_NAME, _child_collection_definition())
    _ensure_collection(database, PARENT_COLLECTION_NAME, _parent_collection_definition())

    print(f"Initializing vector store collection '{CHILD_COLLECTION_NAME}' with LangChain...")
    try:
        def _create_vector_store(embedding_instance: Any) -> AstraDBVectorStore:
            # Avoid autodetect mode because the installed langchain_astradb version
            # surveys collections through a deprecated Data API command.
            return AstraDBVectorStore(
                embedding=embedding_instance,
                collection_name=CHILD_COLLECTION_NAME,
                token=ASTRA_DB_TOKEN,
                api_endpoint=ASTRA_DB_URL,
                namespace=ASTRA_DB_KEYSPACE,
                setup_mode=SetupMode.OFF,
                content_field="content",
            )

        vector_store = _create_vector_store(EMBEDDINGS_INSTANCE)
        collections["vector_store"] = vector_store
        print(f"Vector store initialized for '{CHILD_COLLECTION_NAME}'.")
    except Exception as exc:
        provider = (os.getenv("EMBEDDING_PROVIDER", "LOCAL") or "").strip().upper()
        if provider == "BEAM":
            print(
                "Failed to initialize AstraDBVectorStore with BEAM embeddings. "
                "Retrying with LOCAL embeddings."
            )
            fallback_embeddings = _build_local_embeddings_instance()
            vector_store = _create_vector_store(fallback_embeddings)
            collections["vector_store"] = vector_store
            print(
                f"Vector store initialized for '{CHILD_COLLECTION_NAME}' "
                "using LOCAL fallback embeddings."
            )
        else:
            print(f"Failed to initialize AstraDBVectorStore: {exc}")
            raise

    print(f"Initializing document store collection '{PARENT_COLLECTION_NAME}'...")
    try:
        parent_store = AstraParentDocumentStore(
            collection_name=PARENT_COLLECTION_NAME,
            token=ASTRA_DB_TOKEN,
            api_endpoint=ASTRA_DB_URL,
        )
        collections["parent_store"] = parent_store
        print(f"Parent document store initialized for '{PARENT_COLLECTION_NAME}'.")
    except Exception as exc:
        print(f"Failed to initialize Astra parent document store: {exc}")
        raise

    return collections
