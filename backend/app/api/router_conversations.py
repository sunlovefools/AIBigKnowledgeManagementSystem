import os
from datetime import datetime, timezone
from typing import Any

from astrapy import DataAPIClient
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_user

router = APIRouter()

# TODO: We should not use global variables for database state
_CONVERSATIONS_COLLECTION = None
_CHAT_MESSAGES_COLLECTION = None

class ConversationSummary(BaseModel):
    conversationId: str
    title: str
    updatedAt: str
    messageCount: int = 0
    lastMessage: dict[str, Any] | None = None


class ConversationsListResponse(BaseModel):
    conversations: list[ConversationSummary] = Field(default_factory=list)


class ChatMessageOut(BaseModel):
    messageId: str | None = None
    role: str
    text: str
    timestamp: str
    userId: str | None = None
    userEmail: str | None = None


class ConversationMetaOut(BaseModel):
    conversationId: str
    title: str
    createdAt: str | None = None
    updatedAt: str
    messageCount: int = 0


class ConversationMessagesResponse(BaseModel):
    conversation: ConversationMetaOut
    messages: list[ChatMessageOut] = Field(default_factory=list)
    hasMore: bool = False
    nextCursor: int | None = None


class RenameConversationRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class RenameConversationResponse(BaseModel):
    conversationId: str
    title: str
    updatedAt: str

# -- Helper functions -- 
def _normalized_email(value: str | None) -> str:
    return str(value or "").strip().lower()


def _owner_filter(user_id: str, user_email: str | None) -> dict[str, Any]:
    """Returns a MongoDB filter dict to find documents owned by the user, matching either userId or userEmail."""

    #TODO: Don't pass the email at all since it is not used
    _ = user_email
    return {"userId": user_id}


def _conversation_owner_filter(
    conversation_id: str, user_id: str, user_email: str | None
) -> dict[str, Any]:
    return {
        "$and": [
            {"conversationId": conversation_id},
            _owner_filter(user_id, user_email),
        ]
    }

# -- Global variables for database collections --
def _get_chat_database():
    astra_db_url = os.getenv("ASTRA_CHAT_DB_URL") or os.getenv("ASTRA_DB_URL")
    astra_db_token = os.getenv("ASTRA_CHAT_DB_TOKEN") or os.getenv("ASTRA_DB_TOKEN")
    astra_db_keyspace = os.getenv("ASTRA_CHAT_KEYSPACE") or os.getenv("ASTRA_DB_KEYSPACE")

    if not astra_db_url or not astra_db_token:
        return None

    client = DataAPIClient()
    return client.get_database(astra_db_url, token=astra_db_token, keyspace=astra_db_keyspace)


def _get_conversations_collection():
    global _CONVERSATIONS_COLLECTION
    if _CONVERSATIONS_COLLECTION is not None:
        return _CONVERSATIONS_COLLECTION

    conversations_collection_name = os.getenv("ASTRA_CONVERSATIONS_COLLECTION", "conversations")
    database = _get_chat_database()
    if database is None:
        return None

    _CONVERSATIONS_COLLECTION = database.get_collection(conversations_collection_name)
    return _CONVERSATIONS_COLLECTION


def _get_chat_messages_collection():
    global _CHAT_MESSAGES_COLLECTION
    if _CHAT_MESSAGES_COLLECTION is not None:
        return _CHAT_MESSAGES_COLLECTION

    chat_collection_name = os.getenv("ASTRA_CHAT_COLLECTION", "chat_messages")
    database = _get_chat_database()
    if database is None:
        return None

    _CHAT_MESSAGES_COLLECTION = database.get_collection(chat_collection_name)
    return _CHAT_MESSAGES_COLLECTION

# -- Endpoints --
# Endpoint for listing conversations for the current user, with pagination support
@router.get("/conversations", response_model=ConversationsListResponse)
def list_conversations(
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user.get("sub") or "").strip()
    user_email = _normalized_email(str(current_user.get("email") or ""))
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    collection = _get_conversations_collection()
    if collection is None:
        raise HTTPException(status_code=500, detail="Conversations store is unavailable")

    # Find the most recent conversations for the user, sorted by updatedAt descending
    docs = list(collection.find(_owner_filter(user_id, user_email), limit=limit))
    docs.sort(key=lambda doc: doc.get("updatedAt", ""), reverse=True)

    # Return the conversation
    return ConversationsListResponse(
        conversations=[
            ConversationSummary(
                conversationId=doc.get("conversationId", ""),
                title=doc.get("title", "New conversation"),
                updatedAt=doc.get("updatedAt", ""),
                messageCount=int(doc.get("messageCount", 0)),
                lastMessage=doc.get("lastMessage"),
            )
            for doc in docs
            if doc.get("conversationId")
        ]
    )

# Endpoint for retrieving messages in a conversation, with pagination support
@router.get("/conversations/{conversation_id}/messages", response_model=ConversationMessagesResponse)
def get_conversation_messages(
    conversation_id: str,
    limit: int = Query(20, ge=1, le=100),
    cursor: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user.get("sub") or "").strip()
    user_email = _normalized_email(str(current_user.get("email") or ""))
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    conversations_collection = _get_conversations_collection()
    chat_collection = _get_chat_messages_collection()

    if conversations_collection is None or chat_collection is None:
        raise HTTPException(status_code=500, detail="Conversation stores are unavailable")

    conversation = conversations_collection.find_one(
        _conversation_owner_filter(conversation_id, user_id, user_email)
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    message_count = int(conversation.get("messageCount", 0))
    raw_docs = list(
        chat_collection.find(
            _conversation_owner_filter(conversation_id, user_id, user_email),
            sort={"timestamp": -1},
            skip=cursor,
            limit=limit + 1,
        )
    )

    has_more = len(raw_docs) > limit
    paged_docs = raw_docs[:limit]
    paged_docs.reverse()
    next_cursor = cursor + limit if has_more else None

    return ConversationMessagesResponse(
        conversation=ConversationMetaOut(
            conversationId=conversation_id,
            title=conversation.get("title", "New conversation"),
            createdAt=conversation.get("createdAt"),
            updatedAt=conversation.get("updatedAt", ""),
            messageCount=message_count,
        ),
        messages=[
            ChatMessageOut(
                messageId=str(doc.get("_id")) if doc.get("_id") is not None else None,
                role=doc.get("role", ""),
                text=doc.get("text", ""),
                timestamp=doc.get("timestamp", ""),
                userId=doc.get("userId"),
                userEmail=doc.get("userEmail"),
            )
            for doc in paged_docs
        ],
        hasMore=has_more,
        nextCursor=next_cursor,
    )


@router.patch("/conversations/{conversation_id}/title", response_model=RenameConversationResponse)
def rename_conversation(
    conversation_id: str,
    request: RenameConversationRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user.get("sub") or "").strip()
    user_email = _normalized_email(str(current_user.get("email") or ""))
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    conversations_collection = _get_conversations_collection()
    if conversations_collection is None:
        raise HTTPException(status_code=500, detail="Conversations store is unavailable")

    conversation = conversations_collection.find_one(
        _conversation_owner_filter(conversation_id, user_id, user_email)
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    timestamp = datetime.now(timezone.utc).isoformat()
    update_result = conversations_collection.update_one(
        _conversation_owner_filter(conversation_id, user_id, user_email),
        {"$set": {"title": request.title.strip(), "updatedAt": timestamp, "userId": user_id}},
    )

    if update_result is None:
        raise HTTPException(status_code=500, detail="Failed to update conversation")

    return RenameConversationResponse(
        conversationId=conversation_id,
        title=request.title.strip(),
        updatedAt=timestamp,
    )
