import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from astrapy import DataAPIClient
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_user
from app.service.rag.retrieval.answer_generator import generate_answer
from app.service.rag.retrieval.query_refiner import refine_query
from app.vectordb.vectordb import search_and_retrieve_context

router = APIRouter()

_CHAT_COLLECTION = None
_CONVERSATIONS_COLLECTION = None


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


def _is_owned_conversation(doc: dict[str, Any] | None, user_id: str, user_email: str | None) -> bool:
    _ = user_email
    if not isinstance(doc, dict):
        return False

    doc_user_id = str(doc.get("userId") or "").strip()
    return bool(doc_user_id and doc_user_id == user_id)


def _assert_conversation_ownership(conversation_id: str, user_id: str, user_email: str | None) -> None:
    collection = _get_conversations_collection()
    if collection is None:
        return

    existing_any = collection.find_one({"conversationId": conversation_id})
    if existing_any and not _is_owned_conversation(existing_any, user_id, user_email):
        raise PermissionError("Forbidden for this conversation")


def _get_chat_database():
    astra_db_url = os.getenv("ASTRA_CHAT_DB_URL") or os.getenv("ASTRA_DB_URL")
    astra_db_token = os.getenv("ASTRA_CHAT_DB_TOKEN") or os.getenv("ASTRA_DB_TOKEN")
    astra_db_keyspace = os.getenv("ASTRA_CHAT_KEYSPACE") or os.getenv("ASTRA_DB_KEYSPACE")

    if not astra_db_url or not astra_db_token:
        return None

    client = DataAPIClient()
    return client.get_database(astra_db_url, token=astra_db_token, keyspace=astra_db_keyspace)


def _get_chat_collection():
    global _CHAT_COLLECTION
    if _CHAT_COLLECTION is not None:
        return _CHAT_COLLECTION

    chat_collection_name = os.getenv("ASTRA_CHAT_COLLECTION", "chat_messages")

    try:
        database = _get_chat_database()
        if database is None:
            print("Chat collection disabled: Missing ASTRA DB credentials")
            return None

        _CHAT_COLLECTION = database.get_collection(chat_collection_name)
        print(f"Chat collection '{chat_collection_name}' connected")
        return _CHAT_COLLECTION
    except Exception as e:
        print(f"Chat collection init failed: {e}")
        return None


def _get_conversations_collection():
    global _CONVERSATIONS_COLLECTION
    if _CONVERSATIONS_COLLECTION is not None:
        return _CONVERSATIONS_COLLECTION

    conversations_collection_name = os.getenv("ASTRA_CONVERSATIONS_COLLECTION", "conversations")

    try:
        database = _get_chat_database()
        if database is None:
            print("Conversations collection disabled: Missing ASTRA DB credentials")
            return None

        _CONVERSATIONS_COLLECTION = database.get_collection(conversations_collection_name)
        print(f"Conversations collection '{conversations_collection_name}' connected")
        return _CONVERSATIONS_COLLECTION
    except Exception as e:
        print(f"Conversations collection init failed: {e}")
        return None


def _enforce_message_limit(
    conversation_id: str,
    user_id: str,
    user_email: str | None = None,
    max_messages: int = 100,
):
    chat_collection = _get_chat_collection()
    conversations_collection = _get_conversations_collection()

    if chat_collection is None or conversations_collection is None:
        return

    try:
        message_filter = _conversation_owner_filter(conversation_id, user_id, user_email)
        count = chat_collection.count_documents(message_filter)

        if count > max_messages:
            oldest_messages = list(
                chat_collection.find(
                    message_filter,
                    sort={"timestamp": 1},
                    limit=count - max_messages,
                )
            )

            for msg in oldest_messages:
                chat_collection.delete_one({"_id": msg.get("_id")})

            conversations_collection.update_one(
                _conversation_owner_filter(conversation_id, user_id, user_email),
                {"$inc": {"messageCount": -(count - max_messages)}},
            )
            print(f"Replaced {count - max_messages} oldest messages from conversation {conversation_id}")
    except Exception as e:
        print(f"Failed to enforce message limit: {e}")


def _enforce_conversation_limit(
    user_id: str,
    user_email: str | None = None,
    max_conversations: int = 20,
):
    chat_collection = _get_chat_collection()
    conversations_collection = _get_conversations_collection()

    if conversations_collection is None:
        return

    try:
        owner_filter = _owner_filter(user_id, user_email)
        count = conversations_collection.count_documents(owner_filter)

        if count > max_conversations:
            oldest_conversations = list(
                conversations_collection.find(
                    owner_filter,
                    sort={"createdAt": 1},
                    limit=count - max_conversations,
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
):
    collection = _get_conversations_collection()
    if collection is None:
        return None

    timestamp = datetime.now(timezone.utc).isoformat()
    normalized_email = _normalized_email(user_email)
    existing = collection.find_one({"conversationId": conversation_id})
    if existing and not _is_owned_conversation(existing, user_id, user_email):
        raise PermissionError("Forbidden for this conversation")

    if existing:
        current_count = int(existing.get("messageCount", 0))
        existing_title = existing.get("title", "New conversation")
        should_replace_title = role == "user" and (
            not existing_title or existing_title == "New conversation"
        )
        next_title = _generate_conversation_title(text) if should_replace_title else existing_title

        update_result = collection.update_one(
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
    collection.insert_one(conversation_document)
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
):
    collection = _get_chat_collection()
    if collection is None:
        return None

    _assert_conversation_ownership(conversation_id, user_id, user_email)

    timestamp = datetime.now(timezone.utc).isoformat()
    normalized_email = _normalized_email(user_email)
    chat_document = {
        "conversationId": conversation_id,
        "userId": user_id,
        "userEmail": normalized_email or None,
        "role": role,
        "text": text,
        "timestamp": timestamp,
    }

    result = collection.insert_one(chat_document)

    is_new_conversation = False
    try:
        metadata_result = _upsert_conversation_metadata(
            conversation_id=conversation_id,
            user_id=user_id,
            user_email=normalized_email,
            role=role,
            text=text,
        )
        is_new_conversation = metadata_result and metadata_result.get("created", False)
    except PermissionError:
        raise
    except Exception as e:
        print(f"Failed to update conversation metadata: {e}")

    try:
        _enforce_message_limit(conversation_id, user_id, normalized_email)
    except Exception as e:
        print(f"Failed to enforce message limit: {e}")

    if is_new_conversation:
        try:
            _enforce_conversation_limit(user_id, normalized_email)
        except Exception as e:
            print(f"Failed to enforce conversation limit: {e}")

    return {
        "messageId": str(result.inserted_id),
        "conversationId": conversation_id,
        "userId": user_id,
        "userEmail": normalized_email or None,
        "role": role,
        "text": text,
        "timestamp": timestamp,
    }


class QueryRequest(BaseModel):
    query: str
    top_k: int = 20
    conversation_id: str | None = None


class QueryResponse(BaseModel):
    answer: str
    conversation_id: str
    saved_messages: list[dict] = Field(default_factory=list)


@router.get("/health")
def query_health():
    return {"query_service": "ok"}


@router.post("/query", response_model=QueryResponse)
async def query_documents(
    request: QueryRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    user_email = _normalized_email(str(current_user.get("email") or ""))
    conversation_id = request.conversation_id or str(uuid4())

    saved_messages: list[dict] = []

    try:
        saved_user_message = _save_chat_message(
            conversation_id=conversation_id,
            user_id=user_id,
            user_email=user_email,
            role="user",
            text=request.query,
        )
        if saved_user_message is not None:
            saved_messages.append(saved_user_message)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        print(f"Failed to persist user chat message: {e}")

    try:
        rag_docs = await search_and_retrieve_context(
            query=request.query,
            top_k=request.top_k,
            user_id=user_id,
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
