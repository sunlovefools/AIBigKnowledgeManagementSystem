from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_user
from app.core.db_dependencies import (
    get_chat_messages_collection,
    get_conversations_collection,
)

router = APIRouter()

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
    searchScope: str | None = None
    collectionId: str | None = None
    collectionName: str | None = None


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
def _owner_filter(user_id: str) -> dict[str, Any]:
    """Returns a MongoDB filter dict for documents owned by the userId."""
    return {"userId": user_id}


def _conversation_owner_filter(
    conversation_id: str, user_id: str
) -> dict[str, Any]:
    """Returns a MongoDB filter dict for documents with the conversationId and owned by the userId."""
    return {
        "$and": [
            {"conversationId": conversation_id},
            _owner_filter(user_id),
        ]
    }

# -- Endpoints --
# Endpoint for listing conversations for the current user, sorted by updatedAt desc
@router.get("/conversations", response_model=ConversationsListResponse)
def list_conversations(
    current_user: dict = Depends(get_current_user),
    conversations_collection: Any = Depends(get_conversations_collection),
):
    user_id = str(current_user.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    if conversations_collection is None:
        raise HTTPException(status_code=500, detail="Conversations store is unavailable")

    # Fetch the conversations for the user, sorted by updatedAt desc
    docs = list(
        conversations_collection.find(
            _owner_filter(user_id),
            sort={"updatedAt": -1},
        )
    )

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

# Endpoint for retrieving messages in a conversation, sorted by timestamp asc
@router.get("/conversations/{conversation_id}/messages", response_model=ConversationMessagesResponse)
def get_conversation_messages(
    conversation_id: str,
    current_user: dict = Depends(get_current_user),
    conversations_collection: Any = Depends(get_conversations_collection),
    chat_collection: Any = Depends(get_chat_messages_collection),
):
    user_id = str(current_user.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    if conversations_collection is None or chat_collection is None:
        raise HTTPException(status_code=500, detail="Conversation stores are unavailable")

    conversation = conversations_collection.find_one(
        _conversation_owner_filter(conversation_id, user_id)
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    message_count = int(conversation.get("messageCount", 0))

    # Fetch all messages for the conversation in chronological order.
    message_docs = list(
        chat_collection.find(
            _conversation_owner_filter(conversation_id, user_id),
            sort={"timestamp": 1},
        )
    )

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
                searchScope=doc.get("searchScope"),
                collectionId=doc.get("collectionId"),
                collectionName=doc.get("collectionName"),
            )
            for doc in message_docs
        ],
        hasMore=False,
        nextCursor=None,
    )

# Endpoint for renaming a conversation (only title can be updated for now)
@router.patch("/conversations/{conversation_id}/title", response_model=RenameConversationResponse)
def rename_conversation(
    conversation_id: str,
    request: RenameConversationRequest,
    current_user: dict = Depends(get_current_user),
    conversations_collection: Any = Depends(get_conversations_collection),
):
    user_id = str(current_user.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    if conversations_collection is None:
        raise HTTPException(status_code=500, detail="Conversations store is unavailable")

    conversation = conversations_collection.find_one(
        _conversation_owner_filter(conversation_id, user_id)
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    timestamp = datetime.now(timezone.utc).isoformat()
    update_result = conversations_collection.update_one(
        _conversation_owner_filter(conversation_id, user_id ),
        {"$set": {"title": request.title.strip(), "updatedAt": timestamp, "userId": user_id}},
    )

    if update_result is None:
        raise HTTPException(status_code=500, detail="Failed to update conversation")

    return RenameConversationResponse(
        conversationId=conversation_id,
        title=request.title.strip(),
        updatedAt=timestamp,
    )
