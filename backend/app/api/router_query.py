from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.db_dependencies import (
    get_chat_messages_collection,
    get_conversations_collection,
)
from app.core.dependencies import get_current_user
from app.service.collection.collection_service import (
    CollectionNotFoundError,
    CollectionService,
    CollectionServiceError,
)
from app.service.rag.retrieval.answer_generator import generate_answer
from app.service.rag.retrieval.query_refiner import refine_query
from app.vectordb.vectordb import search_and_retrieve_context

router = APIRouter()

MAX_MESSAGES_PER_CONVERSATION = 20
MAX_CONVERSATIONS_PER_USER = 20
COUNT_DOCUMENTS_UPPER_BOUND = 100_000


class MessageLimitExceededError(Exception):
    pass


def _normalized_email(value: str | None) -> str:
    return str(value or "").strip().lower()


def _owner_variants(user_id: str, user_email: str | None) -> list[dict[str, Any]]:
    _ = user_email
    return [{"userId": user_id}]


def _owner_filter(user_id: str, user_email: str | None) -> dict[str, Any]:
    variants = _owner_variants(user_id, user_email)
    if len(variants) == 1:
        return variants[0]
    return {"$or": variants}


def _conversation_owner_filter(
    conversation_id: str, user_id: str, user_email: str | None
) -> dict[str, Any]:
    return {
        "$and": [
            {"conversationId": conversation_id},
            _owner_filter(user_id, user_email),
        ]
    }


def _count_documents(
    collection: Any,
    filter_doc: dict[str, Any],
    *,
    upper_bound: int = COUNT_DOCUMENTS_UPPER_BOUND,
) -> int:
    """
    Compatibility wrapper for Astra Data API and Mongo-style collections.
    """
    try:
        return int(collection.count_documents(filter_doc, upper_bound=upper_bound))
    except TypeError as exc:
        # PyMongo/mocks may not support Astra's required `upper_bound` keyword.
        if "upper_bound" not in str(exc):
            raise
        return int(collection.count_documents(filter_doc))


def _is_owned_conversation(doc: dict[str, Any] | None, user_id: str, user_email: str | None) -> bool:
    _ = user_email
    if not isinstance(doc, dict):
        return False

    doc_user_id = str(doc.get("userId") or "").strip()
    return bool(doc_user_id and doc_user_id == user_id)


def _assert_conversation_ownership(
    conversation_id: str,
    user_id: str,
    user_email: str | None,
    conversations_collection: Any,
) -> None:
    if conversations_collection is None:
        return

    existing_any = conversations_collection.find_one({"conversationId": conversation_id})
    if existing_any and not _is_owned_conversation(existing_any, user_id, user_email):
        raise PermissionError("Forbidden for this conversation")


def _enforce_message_limit(
    conversation_id: str,
    user_id: str,
    chat_collection: Any,
    conversations_collection: Any,
    user_email: str | None = None,
    max_messages: int = 20,
    reserve_slots: int = 0,
):
    if chat_collection is None or conversations_collection is None:
        return

    message_filter = _conversation_owner_filter(conversation_id, user_id, user_email)
    count = _count_documents(chat_collection, message_filter)

    effective_limit = max(0, max_messages - max(0, reserve_slots))
    if count > effective_limit:
        raise MessageLimitExceededError(
            "This conversation has reached the 20-message limit. Please start a new conversation."
        )


def _enforce_conversation_limit(
    user_id: str,
    chat_collection: Any,
    conversations_collection: Any,
    user_email: str | None = None,
    max_conversations: int = 20,
    reserve_slots: int = 0,
):
    """
    """
    if conversations_collection is None:
        return

    try:
        owner_filter = _owner_filter(user_id, user_email)
        count = _count_documents(conversations_collection, owner_filter)

        effective_limit = max(0, max_conversations - max(0, reserve_slots))
        if count > effective_limit:
            oldest_conversations = list(
                conversations_collection.find(
                    owner_filter,
                    sort={"createdAt": 1},
                    limit=count - effective_limit,
                )
            )

            for conv in oldest_conversations:
                old_conv_id = conv.get("conversationId")
                conversations_collection.delete_one({"_id": conv.get("_id")})

                if chat_collection and old_conv_id:
                    chat_collection.delete_many(
                        _conversation_owner_filter(str(old_conv_id), user_id, user_email)
                    )

                print(f"Deleted oldest conversation {old_conv_id} for user_id {user_id}")
    except Exception as e:
        print(f"Failed to enforce conversation limit: {e}")


def _generate_conversation_title(first_user_message: str):
    normalized = " ".join(first_user_message.strip().split())
    if not normalized:
        return "New conversation"

    max_length = 60
    return normalized[:max_length].rstrip() + ("..." if len(normalized) > max_length else "")


def _upsert_conversation_metadata(
    conversation_id: str,
    user_id: str,
    user_email: str | None,
    role: str,
    text: str,
    conversations_collection: Any,
):
    if conversations_collection is None:
        return None

    timestamp = datetime.now(timezone.utc).isoformat()
    normalized_email = _normalized_email(user_email)
    existing = conversations_collection.find_one({"conversationId": conversation_id})
    if existing and not _is_owned_conversation(existing, user_id, user_email):
        raise PermissionError("Forbidden for this conversation")

    if existing:
        current_count = int(existing.get("messageCount", 0))
        existing_title = existing.get("title", "New conversation")
        should_replace_title = role == "user" and (
            not existing_title or existing_title == "New conversation"
        )
        next_title = _generate_conversation_title(text) if should_replace_title else existing_title

        update_result = conversations_collection.update_one(
            _conversation_owner_filter(conversation_id, user_id, user_email),
            {
                "$set": {
                    "userId": user_id,
                    "userEmail": normalized_email or None,
                    "title": next_title,
                    "updatedAt": timestamp,
                    "lastMessage": {
                        "role": role,
                        "text": text,
                        "timestamp": timestamp,
                    },
                },
                "$inc": {"messageCount": 1},
            },
        )
        return {
            "conversationId": conversation_id,
            "createdAt": existing.get("createdAt"),
            "updatedAt": timestamp,
            "messageCount": current_count + 1,
            "title": next_title,
            "updated": update_result is not None,
        }

    title = _generate_conversation_title(text) if role == "user" else "New conversation"
    conversation_document = {
        "conversationId": conversation_id,
        "userId": user_id,
        "userEmail": normalized_email or None,
        "title": title,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "messageCount": 1,
        "lastMessage": {
            "role": role,
            "text": text,
            "timestamp": timestamp,
        },
    }
    conversations_collection.insert_one(conversation_document)
    return {
        "conversationId": conversation_id,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "messageCount": 1,
        "title": title,
        "created": True,
    }


def _save_chat_message(
    conversation_id: str,
    user_id: str,
    user_email: str | None,
    role: str,
    text: str,
    chat_collection: Any,
    conversations_collection: Any,
    search_scope: str | None = None,
    collection_id: str | None = None,
    collection_name: str | None = None,
    progress_trace: dict[str, Any] | None = None,
):
    if chat_collection is None:
        return None

    _assert_conversation_ownership(
        conversation_id,
        user_id,
        user_email,
        conversations_collection,
    )

    timestamp = datetime.now(timezone.utc).isoformat()
    normalized_email = _normalized_email(user_email)

    existing_conversation = None
    if conversations_collection is not None:
        existing_conversation = conversations_collection.find_one(
            _conversation_owner_filter(conversation_id, user_id, normalized_email)
        )

    # Strict caps: make room before writing new records.
    if existing_conversation is None:
        _enforce_conversation_limit(
            user_id,
            chat_collection,
            conversations_collection,
            normalized_email,
            max_conversations=MAX_CONVERSATIONS_PER_USER,
            reserve_slots=1,
        )
    else:
        _enforce_message_limit(
            conversation_id,
            user_id,
            chat_collection,
            conversations_collection,
            normalized_email,
            max_messages=MAX_MESSAGES_PER_CONVERSATION,
            reserve_slots=1,
        )

    chat_document = {
        "conversationId": conversation_id,
        "userId": user_id,
        "userEmail": normalized_email or None,
        "role": role,
        "text": text,
        "timestamp": timestamp,
    }
    if search_scope:
        chat_document["searchScope"] = search_scope
        chat_document["collectionId"] = collection_id
        chat_document["collectionName"] = collection_name
    if isinstance(progress_trace, dict) and progress_trace:
        chat_document["progressTrace"] = progress_trace

    result = chat_collection.insert_one(chat_document)

    try:
        _upsert_conversation_metadata(
            conversation_id=conversation_id,
            user_id=user_id,
            user_email=normalized_email,
            role=role,
            text=text,
            conversations_collection=conversations_collection,
        )
    except PermissionError:
        raise
    except Exception as e:
        print(f"Failed to update conversation metadata: {e}")

    return {
        "messageId": str(result.inserted_id),
        "conversationId": conversation_id,
        "userId": user_id,
        "userEmail": normalized_email or None,
        "role": role,
        "text": text,
        "timestamp": timestamp,
        "searchScope": search_scope,
        "collectionId": collection_id,
        "collectionName": collection_name,
        "progressTrace": progress_trace if isinstance(progress_trace, dict) and progress_trace else None,
    }


class QueryRequest(BaseModel):
    query: str
    top_k: int = 20
    conversation_id: str | None = None
    collectionId: str | None = None
    collectionName: str | None = None
    searchScope: Literal["collection", "all_collections"] = "collection"


class QueryResponse(BaseModel):
    answer: str
    conversation_id: str
    saved_messages: list[dict] = Field(default_factory=list)


class ConversationTurnPersistRequest(BaseModel):
    user_text: str = Field(..., min_length=1)
    ai_text: str = Field(..., min_length=1)
    conversation_id: str | None = None
    searchScope: Literal["collection", "all_collections"] | None = None
    collectionId: str | None = None
    collectionName: str | None = None
    progressTrace: dict[str, Any] | None = None


class ConversationTurnPersistResponse(BaseModel):
    conversation_id: str
    saved_messages: list[dict] = Field(default_factory=list)


@router.get("/health")
def query_health():
    return {"query_service": "ok"}


@router.post("/conversations/turn", response_model=ConversationTurnPersistResponse)
async def persist_conversation_turn(
    request: ConversationTurnPersistRequest,
    current_user: dict = Depends(get_current_user),
    chat_collection: Any = Depends(get_chat_messages_collection),
    conversations_collection: Any = Depends(get_conversations_collection),
):
    user_id = str(current_user.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    user_email = _normalized_email(str(current_user.get("email") or ""))
    conversation_id = request.conversation_id or str(uuid4())

    if request.conversation_id and chat_collection is not None:
        existing_count = _count_documents(
            chat_collection,
            _conversation_owner_filter(conversation_id, user_id, user_email),
        )
        if existing_count >= MAX_MESSAGES_PER_CONVERSATION - 1:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This conversation has reached the 20-message limit. "
                    "Please start a new conversation."
                ),
            )

    saved_messages: list[dict] = []
    try:
        saved_user_message = _save_chat_message(
            conversation_id=conversation_id,
            user_id=user_id,
            user_email=user_email,
            role="user",
            text=request.user_text.strip(),
            chat_collection=chat_collection,
            conversations_collection=conversations_collection,
            search_scope=request.searchScope,
            collection_id=request.collectionId,
            collection_name=request.collectionName,
        )
        if saved_user_message is not None:
            saved_messages.append(saved_user_message)

        saved_ai_message = _save_chat_message(
            conversation_id=conversation_id,
            user_id=user_id,
            user_email=user_email,
            role="ai",
            text=request.ai_text.strip(),
            chat_collection=chat_collection,
            conversations_collection=conversations_collection,
            search_scope=request.searchScope,
            collection_id=request.collectionId,
            collection_name=request.collectionName,
            progress_trace=request.progressTrace,
        )
        if saved_ai_message is not None:
            saved_messages.append(saved_ai_message)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except MessageLimitExceededError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        print(f"Failed to persist conversation turn: {e}")
        raise HTTPException(status_code=500, detail="Failed to persist conversation turn.")

    return ConversationTurnPersistResponse(
        conversation_id=conversation_id,
        saved_messages=saved_messages,
    )


@router.post("/query", response_model=QueryResponse)
async def query_documents(
    request: QueryRequest,
    current_user: dict = Depends(get_current_user),
    chat_collection: Any = Depends(get_chat_messages_collection),
    conversations_collection: Any = Depends(get_conversations_collection),
):
    user_id = str(current_user.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    user_email = _normalized_email(str(current_user.get("email") or ""))
    conversation_id = request.conversation_id or str(uuid4())
    scoped_file_ids: list[str] | None = None
    scope_collection_id: str | None = None
    scope_collection_name: str | None = None
    if request.searchScope == "collection":
        try:
            active_collection = await CollectionService.resolve_active_collection(
                user_id=user_id,
                requested_collection_id=request.collectionId,
            )
            scope_collection_id = str(active_collection.get("collection_id") or "").strip() or None
            scope_collection_name = str(
                active_collection.get("name") or request.collectionName or ""
            ).strip() or None
            scoped_file_ids = await CollectionService.list_file_ids_for_collection(
                user_id=user_id,
                collection_id=scope_collection_id or "",
            )
        except CollectionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except CollectionServiceError as e:
            raise HTTPException(status_code=503, detail=str(e))

    # A full query turn usually persists both a user message and an AI message.
    # Reject early when there is not enough capacity left in an existing thread.
    if request.conversation_id and chat_collection is not None:
        existing_count = _count_documents(
            chat_collection,
            _conversation_owner_filter(conversation_id, user_id, user_email)
        )
        if existing_count >= MAX_MESSAGES_PER_CONVERSATION - 1:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This conversation has reached the 20-message limit. "
                    "Please start a new conversation."
                ),
            )

    saved_messages: list[dict] = []

    try:
        saved_user_message = _save_chat_message(
            conversation_id=conversation_id,
            user_id=user_id,
            user_email=user_email,
            role="user",
            text=request.query,
            chat_collection=chat_collection,
            conversations_collection=conversations_collection,
            search_scope=request.searchScope,
            collection_id=scope_collection_id,
            collection_name=scope_collection_name,
        )
        if saved_user_message is not None:
            saved_messages.append(saved_user_message)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except MessageLimitExceededError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        print(f"Failed to persist user chat message: {e}")
        raise HTTPException(status_code=500, detail="Failed to persist user chat message.")

    try:
        rag_docs = await search_and_retrieve_context(
            query=request.query,
            top_k=request.top_k,
            user_id=user_id,
            included_file_ids=scoped_file_ids,
        )

        if not rag_docs:
            no_docs_answer = "No relevant documents found for your query. Try ingesting more data."
            try:
                saved_ai_message = _save_chat_message(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    user_email=user_email,
                    role="ai",
                    text=no_docs_answer,
                    chat_collection=chat_collection,
                    conversations_collection=conversations_collection,
                    search_scope=request.searchScope,
                    collection_id=scope_collection_id,
                    collection_name=scope_collection_name,
                )
                if saved_ai_message is not None:
                    saved_messages.append(saved_ai_message)
            except Exception as e:
                print(f"Failed to persist AI chat message: {e}")

            return QueryResponse(
                answer=no_docs_answer,
                conversation_id=conversation_id,
                saved_messages=saved_messages,
            )

    except Exception as e:
        print(f"Retrieval failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Context retrieval failed: {str(e)}",
        )

    try:
        # Kept to avoid behavioral drift if the refine step is re-enabled.
        _ = refine_query
        answer = await generate_answer(rag_docs, request.query)
        print("LLM answer generated")
    except Exception as error:
        print(f"LLM answer generation failed: {error}")
        raise HTTPException(
            status_code=500,
            detail=f"LLM answer generation failed: {str(error)}",
        )

    try:
        saved_ai_message = _save_chat_message(
            conversation_id=conversation_id,
            user_id=user_id,
            user_email=user_email,
            role="ai",
            text=answer,
            chat_collection=chat_collection,
            conversations_collection=conversations_collection,
            search_scope=request.searchScope,
            collection_id=scope_collection_id,
            collection_name=scope_collection_name,
        )
        if saved_ai_message is not None:
            saved_messages.append(saved_ai_message)
    except Exception as e:
        print(f"Failed to persist AI chat message: {e}")

    print("Query process completed successfully")
    return QueryResponse(
        answer=answer,
        conversation_id=conversation_id,
        saved_messages=saved_messages,
    )
