import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _StubZeRankerService:
    async def rerank_documents(self, **_kwargs):
        return []


stub_vectordb_init = types.ModuleType("app.vectordb.vectordb_init")
stub_vectordb_init.init_vector_db = lambda: {
    "vector_store": types.SimpleNamespace(collection=None),
    "parent_store": types.SimpleNamespace(collection=None),
}
stub_vectordb_init.ASTRA_DB_URL = "https://example-astra"
stub_vectordb_init.ASTRA_DB_TOKEN = "test-token"
stub_vectordb_init.CHILD_COLLECTION_NAME = "child_collection"
sys.modules["app.vectordb.vectordb_init"] = stub_vectordb_init

stub_reranker = types.ModuleType("app.service.rag.retrieval.reranker")
stub_reranker.ZeRankerService = _StubZeRankerService
sys.modules["app.service.rag.retrieval.reranker"] = stub_reranker

stub_backend_debug = types.ModuleType("backend.debug.debug_logger")
stub_backend_debug.log_child_chunks = lambda **_kwargs: None
stub_backend_debug.log_reranker_results = lambda **_kwargs: None
sys.modules["backend.debug.debug_logger"] = stub_backend_debug

stub_debug = types.ModuleType("debug.debug_logger")
stub_debug.log_child_chunks = lambda **_kwargs: None
stub_debug.log_reranker_results = lambda **_kwargs: None
sys.modules["debug.debug_logger"] = stub_debug

vectordb = importlib.import_module("app.vectordb.vectordb")


class FakeCollection:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.calls = []

    def find(self, *args, **kwargs):
        self.calls.append({
            "args": args,
            "kwargs": kwargs,
        })
        if self.error is not None:
            raise self.error
        return iter(self.rows)


def test_lexical_search_child_chunks_returns_normalized_rows_in_order(monkeypatch):
    collection = FakeCollection(
        rows=[
            {
                "_id": "child-1",
                "content": "tree on grassy hill",
                "metadata": {"file_metadata": {"file_id": "file-1"}},
                "$lexicalScore": 0.91,
            },
            {
                "_id": "child-2",
                "content": "tree roots",
                "metadata": {"file_metadata": {"file_id": "file-2"}},
                "$lexicalScore": 0.63,
            },
        ]
    )
    monkeypatch.setattr(vectordb, "_get_raw_child_collection", lambda: collection)

    result = asyncio.run(vectordb.lexical_search_child_chunks("tree", user_id="user-1", top_k=2))

    assert result == [
        {
            "_id": "child-1",
            "content": "tree on grassy hill",
            "metadata": {"file_metadata": {"file_id": "file-1"}},
            "lexical_score": 0.91,
        },
        {
            "_id": "child-2",
            "content": "tree roots",
            "metadata": {"file_metadata": {"file_id": "file-2"}},
            "lexical_score": 0.63,
        },
    ]
    assert collection.calls == [
        {
            "args": (),
            "kwargs": {
                "filter": {"metadata.user_id": "user-1"},
                "sort": {"$lexical": "tree"},
                "limit": 2,
            },
        }
    ]


@pytest.mark.parametrize("query", ["tree", "tree hill"])
def test_lexical_search_child_chunks_accepts_single_and_multiword_queries(monkeypatch, query):
    collection = FakeCollection(
        rows=[
            {
                "_id": "child-1",
                "content": "matched text",
                "metadata": {},
            }
        ]
    )
    monkeypatch.setattr(vectordb, "_get_raw_child_collection", lambda: collection)

    result = asyncio.run(vectordb.lexical_search_child_chunks(query, user_id="user-1", top_k=3))

    assert len(result) == 1
    assert collection.calls[0]["kwargs"]["sort"] == {"$lexical": query}
    assert collection.calls[0]["kwargs"]["limit"] == 3


def test_lexical_search_child_chunks_rejects_empty_query():
    with pytest.raises(ValueError, match="query must be a non-empty string"):
        asyncio.run(vectordb.lexical_search_child_chunks("   ", user_id="user-1"))


def test_lexical_search_child_chunks_rejects_empty_user_id():
    with pytest.raises(ValueError, match="user_id must be a non-empty string"):
        asyncio.run(vectordb.lexical_search_child_chunks("tree", user_id="   "))


@pytest.mark.parametrize("top_k", [0, -1])
def test_lexical_search_child_chunks_rejects_non_positive_top_k(top_k):
    with pytest.raises(ValueError, match="top_k must be greater than 0"):
        asyncio.run(vectordb.lexical_search_child_chunks("tree", user_id="user-1", top_k=top_k))


def test_lexical_search_child_chunks_surfaces_lexical_unsupported_error(monkeypatch):
    collection = FakeCollection(error=Exception("unsupported lexical sort"))
    monkeypatch.setattr(vectordb, "_get_raw_child_collection", lambda: collection)

    with pytest.raises(RuntimeError, match="supports lexical search"):
        asyncio.run(vectordb.lexical_search_child_chunks("tree", user_id="user-1"))


def test_lexical_search_child_chunks_defaults_missing_lexical_score_to_none(monkeypatch):
    collection = FakeCollection(
        rows=[
            {
                "_id": "child-1",
                "content": "plain row",
                "metadata": {"file_metadata": {"file_id": "file-1"}},
            }
        ]
    )
    monkeypatch.setattr(vectordb, "_get_raw_child_collection", lambda: collection)

    result = asyncio.run(vectordb.lexical_search_child_chunks("plain", user_id="user-1"))

    assert result == [
        {
            "_id": "child-1",
            "content": "plain row",
            "metadata": {"file_metadata": {"file_id": "file-1"}},
            "lexical_score": None,
        }
    ]


def test_delete_children_by_parent_id_scopes_with_user_id(monkeypatch):
    class _FakeVectorStore:
        def __init__(self):
            self.filters = []

        async def adelete_by_metadata_filter(self, metadata_filter):
            self.filters.append(metadata_filter)
            return 2

    fake_store = _FakeVectorStore()
    monkeypatch.setattr(vectordb, "VECTOR_STORE", fake_store)

    deleted = asyncio.run(vectordb.delete_children_by_parent_id("parent-1", "user-1"))

    assert deleted == 2
    assert fake_store.filters == [
        {
            "child_chunk_metadata.parent_id": "parent-1",
            "user_id": "user-1",
        }
    ]


def test_delete_children_by_parent_id_rejects_empty_user_id():
    with pytest.raises(ValueError, match="user_id must be a non-empty string"):
        asyncio.run(vectordb.delete_children_by_parent_id("parent-1", " "))


def test_delete_parent_document_scopes_with_user_id(monkeypatch):
    class _FakeCollection:
        def __init__(self):
            self.filters = []

        def delete_one(self, filter_doc):
            self.filters.append(filter_doc)

    collection = _FakeCollection()
    monkeypatch.setattr(vectordb, "PARENT_STORE", types.SimpleNamespace(collection=collection))

    asyncio.run(vectordb.delete_parent_document("parent-1", "user-1"))

    assert collection.filters == [
        {
            "_id": "parent-1",
            "value.metadata.user_id": "user-1",
        }
    ]


def test_delete_parent_document_rejects_empty_user_id():
    with pytest.raises(ValueError, match="user_id must be a non-empty string"):
        asyncio.run(vectordb.delete_parent_document("parent-1", ""))


def test_search_and_retrieve_context_uses_user_id_metadata_key(monkeypatch):
    class _FakeVectorStore:
        def __init__(self):
            self.calls = []

        async def asimilarity_search_with_score(self, query, **kwargs):
            self.calls.append({"query": query, **kwargs})
            return []

    fake_store = _FakeVectorStore()
    monkeypatch.setattr(vectordb, "VECTOR_STORE", fake_store)

    result = asyncio.run(
        vectordb.search_and_retrieve_context(
            query="test query",
            top_k=3,
            user_id="user-1",
        )
    )

    assert result == []
    assert fake_store.calls == [
        {
            "query": "test query",
            "k": 3,
            "filter": {"user_id": "user-1"},
        }
    ]
