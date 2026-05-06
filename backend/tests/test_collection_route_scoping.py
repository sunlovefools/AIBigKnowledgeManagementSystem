import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

fake_dependencies = types.ModuleType("app.core.dependencies")
fake_dependencies.get_current_user = lambda: {"sub": "test-user"}
sys.modules["app.core.dependencies"] = fake_dependencies

fake_db_dependencies = types.ModuleType("app.core.db_dependencies")
fake_db_dependencies.get_chat_messages_collection = lambda: None
fake_db_dependencies.get_conversations_collection = lambda: None
fake_db_dependencies.get_user_collections_collection = lambda: None
sys.modules["app.core.db_dependencies"] = fake_db_dependencies

fake_query_refiner = types.ModuleType("app.service.rag.retrieval.query_refiner")
fake_query_refiner.refine_query = lambda query: query
sys.modules["app.service.rag.retrieval.query_refiner"] = fake_query_refiner

fake_answer_generator = types.ModuleType("app.service.rag.retrieval.answer_generator")


async def _fake_generate_answer(_rag_docs, _query):
    return "stub answer"


fake_answer_generator.generate_answer = _fake_generate_answer
sys.modules["app.service.rag.retrieval.answer_generator"] = fake_answer_generator

fake_vectordb = types.ModuleType("app.vectordb.vectordb")


async def _fake_search_and_retrieve_context(**_kwargs):
    return []


fake_vectordb.search_and_retrieve_context = _fake_search_and_retrieve_context
sys.modules["app.vectordb.vectordb"] = fake_vectordb

fake_aiohttp = types.ModuleType("aiohttp")


class _FakeClientSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False


fake_aiohttp.ClientSession = _FakeClientSession
sys.modules["aiohttp"] = fake_aiohttp

from app.api import router_agent, router_query


def test_query_route_threads_collection_scoped_file_ids(monkeypatch):
    captured: dict[str, object] = {}

    async def _resolve_active_collection(*, user_id: str, requested_collection_id: str | None = None):
        _ = user_id, requested_collection_id
        return {"collection_id": "collection-1", "name": "Default"}

    async def _list_file_ids_for_collection(*, user_id: str, collection_id: str):
        _ = user_id, collection_id
        return ["file-1", "file-2"]

    async def _search_and_retrieve_context(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(router_query.CollectionService, "resolve_active_collection", _resolve_active_collection)
    monkeypatch.setattr(router_query.CollectionService, "list_file_ids_for_collection", _list_file_ids_for_collection)
    monkeypatch.setattr(router_query, "search_and_retrieve_context", _search_and_retrieve_context)

    response = asyncio.run(
        router_query.query_documents(
            router_query.QueryRequest(query="hello", collectionId="collection-1"),
            current_user={"sub": "user-1"},
            chat_collection=None,
            conversations_collection=None,
        )
    )

    assert captured["included_file_ids"] == ["file-1", "file-2"]
    assert response.answer.startswith("No relevant documents found")


def test_query_route_all_collections_skips_collection_scope(monkeypatch):
    captured: dict[str, object] = {}

    async def _unexpected_resolve_active_collection(**_kwargs):
        raise AssertionError("all_collections scope must not resolve an active collection")

    async def _unexpected_list_file_ids_for_collection(**_kwargs):
        raise AssertionError("all_collections scope must not list collection file IDs")

    async def _search_and_retrieve_context(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(router_query.CollectionService, "resolve_active_collection", _unexpected_resolve_active_collection)
    monkeypatch.setattr(router_query.CollectionService, "list_file_ids_for_collection", _unexpected_list_file_ids_for_collection)
    monkeypatch.setattr(router_query, "search_and_retrieve_context", _search_and_retrieve_context)

    response = asyncio.run(
        router_query.query_documents(
            router_query.QueryRequest(
                query="hello",
                collectionId="ignored-collection",
                searchScope="all_collections",
            ),
            current_user={"sub": "user-1"},
            chat_collection=None,
            conversations_collection=None,
        )
    )

    assert captured["included_file_ids"] is None
    assert captured["user_id"] == "user-1"
    assert response.answer.startswith("No relevant documents found")


def test_agent_route_intersects_selected_ids_with_collection_scope(monkeypatch):
    captured_state: dict[str, object] = {}

    class _FakeGraph:
        async def ainvoke(self, state):
            captured_state.update(state)
            return {
                **state,
                "intention": "edit",
                "goal": "Scoped edit",
                "lexical_anchors": [],
                "semantic_anchors": [],
                "anchors": [],
                "constraint": "None",
                "proposals": [],
                "node2_search_group_result": {},
                "node3_non_strong_signal_file_context_expansion_result": {},
                "node4_file_filtering_result": {},
                "node5_parent_chunk_constraint_verifier_result": {},
                "node6_editor_result": {},
                "token_prompt_total": 0,
                "token_completion_total": 0,
                "token_total": 0,
                "llm_call_count": 0,
                "error": None,
            }

    async def _resolve_active_collection(*, user_id: str, requested_collection_id: str | None = None):
        _ = user_id, requested_collection_id
        return {"collection_id": "collection-1", "name": "Default"}

    async def _list_file_ids_for_collection(*, user_id: str, collection_id: str):
        _ = user_id, collection_id
        return ["file-a", "file-b"]

    monkeypatch.setattr(router_agent, "log_token_usage", lambda **kwargs: None)
    monkeypatch.setattr(router_agent, "_load_retrieval_graph", lambda: _FakeGraph())
    monkeypatch.setattr(router_agent.CollectionService, "resolve_active_collection", _resolve_active_collection)
    monkeypatch.setattr(router_agent.CollectionService, "list_file_ids_for_collection", _list_file_ids_for_collection)

    request = router_agent.AgenticModificationRequest(
        user_instructions="edit this",
        fileIds=["file-a", "file-z"],
        collectionId="collection-1",
    )
    response = asyncio.run(router_agent._run_agentic_pipeline(request, user_id="user-1"))

    assert captured_state["file_ids"] == ["file-a"]
    assert response.intention == "edit"
