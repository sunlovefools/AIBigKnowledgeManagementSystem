import asyncio
import sys
import types
from pathlib import Path

from fastapi.routing import APIRoute

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

fake_dependencies = types.ModuleType("app.core.dependencies")
fake_dependencies.get_current_user = lambda: {"sub": "test-user"}
sys.modules["app.core.dependencies"] = fake_dependencies

fake_collection_service = types.ModuleType("app.service.collection.collection_service")


class _CollectionNotFoundError(Exception):
    pass


class _CollectionServiceError(Exception):
    pass


class _CollectionService:
    @staticmethod
    async def resolve_active_collection(**_kwargs):
        return {"collection_id": "collection-default", "name": "Default"}

    @staticmethod
    async def list_file_ids_for_collection(**_kwargs):
        return ["file-a", "file-b"]


fake_collection_service.CollectionNotFoundError = _CollectionNotFoundError
fake_collection_service.CollectionServiceError = _CollectionServiceError
fake_collection_service.CollectionService = _CollectionService
sys.modules["app.service.collection.collection_service"] = fake_collection_service

from app.api import router_agent
from app.service.rag.agentic_query.models import AgenticQueryRunResult


def _build_fake_query_helpers(saved_messages: list[dict[str, str]] | None = None):
    class _FakeMessageLimitExceededError(Exception):
        pass

    class _Helpers:
        MAX_MESSAGES_PER_CONVERSATION = 20
        MessageLimitExceededError = _FakeMessageLimitExceededError

        @staticmethod
        def _normalized_email(value: str | None) -> str:
            return str(value or "").strip().lower()

        @staticmethod
        def _conversation_owner_filter(conversation_id: str, user_id: str, user_email: str | None):
            _ = conversation_id, user_id, user_email
            return {}

        @staticmethod
        def _count_documents(collection, filter_doc):
            _ = collection, filter_doc
            return 0

        @staticmethod
        def _save_chat_message(
            conversation_id: str,
            user_id: str,
            user_email: str | None,
            role: str,
            text: str,
            chat_collection,
            conversations_collection,
            search_scope: str | None = None,
            collection_id: str | None = None,
            collection_name: str | None = None,
        ):
            _ = user_email, chat_collection, conversations_collection
            item = {
                "conversationId": conversation_id,
                "userId": user_id,
                "role": role,
                "text": text,
                "searchScope": search_scope,
                "collectionId": collection_id,
                "collectionName": collection_name,
            }
            if isinstance(saved_messages, list):
                saved_messages.append(item)
            return item if chat_collection is not None else None

    return _Helpers


def _build_request(**overrides):
    payload = {
        "query": "What is the refund period?",
        "conversation_id": None,
        "collectionId": None,
        "searchScope": "collection",
        "seed_top_k": 8,
        "max_steps": 12,
    }
    payload.update(overrides)
    return router_agent.AgenticQueryRequest(**payload)


def _patch_collection_scope(monkeypatch, *, file_ids: list[str] | None = None):
    async def _resolve_active_collection(*, user_id: str, requested_collection_id: str | None = None):
        _ = user_id, requested_collection_id
        return {"collection_id": "collection-default", "name": "Default"}

    async def _list_file_ids_for_collection(*, user_id: str, collection_id: str):
        _ = user_id, collection_id
        if file_ids is None:
            return ["file-a", "file-b"]
        return file_ids

    monkeypatch.setattr(router_agent.CollectionService, "resolve_active_collection", _resolve_active_collection)
    monkeypatch.setattr(router_agent.CollectionService, "list_file_ids_for_collection", _list_file_ids_for_collection)


def test_agentic_query_returns_answer_and_persists_messages(monkeypatch):
    captured: dict[str, object] = {}
    saved_messages: list[dict[str, str]] = []

    async def _fake_runner(**kwargs):
        captured.update(kwargs)
        return AgenticQueryRunResult(
            answer="The refund period is 30 days.",
            citations=["policy.md"],
            run_id="run-123",
            termination_reason="finished",
            tool_call_count=2,
        )

    monkeypatch.setattr(router_agent, "_load_agentic_query_runner", lambda: _fake_runner)
    monkeypatch.setattr(
        router_agent,
        "_load_query_helpers",
        lambda: _build_fake_query_helpers(saved_messages),
    )
    _patch_collection_scope(monkeypatch)

    response = asyncio.run(
        router_agent.agentic_query(
            _build_request(),
            current_user={"sub": "user-1", "email": "user@example.com"},
            chat_collection=object(),
            conversations_collection=object(),
        )
    )

    assert captured["included_file_ids"] == ["file-a", "file-b"]
    assert response.answer == "The refund period is 30 days."
    assert response.citations == ["policy.md"]
    assert response.run_id == "run-123"
    assert len(response.saved_messages) == 2
    assert len(saved_messages) == 2
    assert saved_messages[0]["role"] == "user"
    assert saved_messages[1]["role"] == "ai"
    assert saved_messages[0]["searchScope"] == "collection"
    assert saved_messages[0]["collectionId"] == "collection-default"
    assert saved_messages[0]["collectionName"] == "Default"
    assert "(Sources: policy.md)" in saved_messages[1]["text"]


def test_agentic_query_all_collections_scope_skips_collection_file_filter(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_runner(**kwargs):
        captured.update(kwargs)
        return AgenticQueryRunResult(
            answer="The refund period is 30 days.",
            citations=["policy.md"],
            run_id="run-global",
            termination_reason="finished",
            tool_call_count=2,
        )

    async def _unexpected_resolve_active_collection(**_kwargs):
        raise AssertionError("all_collections scope must not resolve an active collection")

    async def _unexpected_list_file_ids_for_collection(**_kwargs):
        raise AssertionError("all_collections scope must not list collection file IDs")

    monkeypatch.setattr(router_agent, "_load_agentic_query_runner", lambda: _fake_runner)
    monkeypatch.setattr(
        router_agent,
        "_load_query_helpers",
        lambda: _build_fake_query_helpers(),
    )
    monkeypatch.setattr(
        router_agent.CollectionService,
        "resolve_active_collection",
        _unexpected_resolve_active_collection,
    )
    monkeypatch.setattr(
        router_agent.CollectionService,
        "list_file_ids_for_collection",
        _unexpected_list_file_ids_for_collection,
    )

    response = asyncio.run(
        router_agent.agentic_query(
            _build_request(searchScope="all_collections", collectionId="ignored-collection"),
            current_user={"sub": "user-1", "email": "user@example.com"},
            chat_collection=None,
            conversations_collection=None,
        )
    )

    assert captured["included_file_ids"] is None
    assert response.answer == "The refund period is 30 days."
    assert response.run_id == "run-global"


def test_agentic_query_stream_emits_progress_and_result(monkeypatch):
    async def _fake_runner(**kwargs):
        progress_callback = kwargs.get("progress_callback")
        if callable(progress_callback):
            await progress_callback(
                {
                    "stage": "agentic_query_pipeline",
                    "status": "started",
                    "message": "started",
                    "metadata": {"runId": "run-stream"},
                }
            )
        return AgenticQueryRunResult(
            answer="Answer from stream.",
            citations=["policy.md"],
            run_id="run-stream",
            termination_reason="finished",
            tool_call_count=1,
        )

    monkeypatch.setattr(router_agent, "_load_agentic_query_runner", lambda: _fake_runner)
    monkeypatch.setattr(
        router_agent,
        "_load_query_helpers",
        lambda: _build_fake_query_helpers(),
    )
    _patch_collection_scope(monkeypatch)

    response = asyncio.run(
        router_agent.agentic_query_stream(
            _build_request(),
            current_user={"sub": "user-1", "email": "user@example.com"},
            chat_collection=None,
            conversations_collection=None,
        )
    )

    async def _read_stream():
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else str(chunk))
        return "".join(chunks)

    payload = asyncio.run(_read_stream())
    assert "event: progress" in payload
    assert "event: result" in payload
    assert "Answer from stream." in payload


def test_agentic_query_routes_registered():
    post_routes = {
        route.path: route.endpoint
        for route in router_agent.router.routes
        if isinstance(route, APIRoute) and "POST" in (route.methods or set())
    }
    assert post_routes["/query"] is router_agent.agentic_query
    assert post_routes["/query-stream"] is router_agent.agentic_query_stream
    assert post_routes["/modify"] is router_agent.agentic_modify
