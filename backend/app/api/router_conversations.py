import os
from typing import Any

from astrapy import DataAPIClient
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter()

_CONVERSATIONS_COLLECTION = None
_CHAT_MESSAGES_COLLECTION = None

"""
Defines the API endpoints for managing conversations and their messages.
"""

# Helper functions to get Astra DB collections (with caching)
def _get_chat_database(): #connection to chat history AstraDB (stores chat messages and conversation metadata collections)
  astra_db_url = os.getenv("ASTRA_CHAT_DB_URL") or os.getenv("ASTRA_DB_URL")
  astra_db_token = os.getenv("ASTRA_CHAT_DB_TOKEN") or os.getenv("ASTRA_DB_TOKEN")
  astra_db_keyspace = os.getenv("ASTRA_CHAT_KEYSPACE") or os.getenv("ASTRA_DB_KEYSPACE")

  if not astra_db_url or not astra_db_token:
    return None

  client = DataAPIClient()
  return client.get_database(astra_db_url, token=astra_db_token, keyspace=astra_db_keyspace)


def _get_conversations_collection(): #connection to conversations metadata DB
  global _CONVERSATIONS_COLLECTION
  if _CONVERSATIONS_COLLECTION is not None:
    return _CONVERSATIONS_COLLECTION

  conversations_collection_name = os.getenv("ASTRA_CONVERSATIONS_COLLECTION", "conversations")
  database = _get_chat_database()
  if database is None:
    return None

  _CONVERSATIONS_COLLECTION = database.get_collection(conversations_collection_name)
  return _CONVERSATIONS_COLLECTION


def _get_chat_messages_collection(): #connection to chat messages DB
  global _CHAT_MESSAGES_COLLECTION
  if _CHAT_MESSAGES_COLLECTION is not None:
    return _CHAT_MESSAGES_COLLECTION

  chat_collection_name = os.getenv("ASTRA_CHAT_COLLECTION", "chat_messages")
  database = _get_chat_database()
  if database is None:
    return None

  _CHAT_MESSAGES_COLLECTION = database.get_collection(chat_collection_name)
  return _CHAT_MESSAGES_COLLECTION


class ConversationSummary(BaseModel): #summary of a conversation, info that will be displayed``
  conversationId: str
  title: str
  updatedAt: str
  messageCount: int = 0
  lastMessage: dict[str, Any] | None = None


class ConversationsListResponse(BaseModel):#response model for listing conversations endpoint
  conversations: list[ConversationSummary] = Field(default_factory=list)#list of conversations, default to empty list if no conversations found


class ChatMessageOut(BaseModel):#response model for a chat message, info that will be stored for each chat message
  messageId: str | None = None
  role: str
  text: str
  timestamp: str
  userEmail: str | None = None


class ConversationMetaOut(BaseModel):#response model for conversation metadata, info that will be stored for each conversation
  conversationId: str
  title: str
  createdAt: str | None = None
  updatedAt: str
  messageCount: int = 0


class ConversationMessagesResponse(BaseModel):#response model for conversation messages endpoint
  conversation: ConversationMetaOut #metadata about the conversation
  messages: list[ChatMessageOut] = Field(default_factory=list)#list of chat messages in the conversation, default to empty list if no messages found
  hasMore: bool = False#indicates if there are more messages to load beyond the current page
  nextCursor: int | None = None#the cursor value to use for the next page of messages (if hasMore is True), null if no more pages


class RenameConversationRequest(BaseModel):#request model for renaming a conversation
  title: str = Field(..., min_length=1, max_length=200)#new title, required, between 1 and 200 characters


class RenameConversationResponse(BaseModel):#response model for rename conversation endpoint
  conversationId: str#ID of the renamed conversation
  title: str#new title of the conversation
  updatedAt: str#timestamp when the conversation was last updated


@router.get("/conversations", response_model=ConversationsListResponse)#endpoint to list conversations for a user, with pagination support
# TODO: We can make the query to be in a model
def list_conversations(#query parameter for user email, required and must be at least 3 characters long; query parameter for limit of conversations to return, default 50, must be between 1 and 200
  user_email: str = Query(..., min_length=3),
  limit: int = Query(50, ge=1, le=200),
):
  collection = _get_conversations_collection()#get connection to conversations collection in Astra DB
  if collection is None:
    raise HTTPException(status_code=500, detail="Conversations store is unavailable")

  docs = list(collection.find({"userEmail": user_email}, limit=limit))#find conversations in the collection that match the user email, limit to the specified number of conversations
  docs.sort(key=lambda doc: doc.get("updatedAt", ""), reverse=True)#sort the conversations by updatedAt timestamp in descending order (most recently updated conversations first)

  return ConversationsListResponse(#return the list of conversations in the response model format
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


@router.get("/conversations/{conversation_id}/messages", response_model=ConversationMessagesResponse)#endpoint to get messages for a conversation, with pagination support
def get_conversation_messages(#path parameter for conversation ID; query parameter for user email, required and must be at least 3 characters long; query parameter for limit of messages to return, default 20, must be between 1 and 100; query parameter for cursor (offset) for pagination, default 0, must be non-negative 
  conversation_id: str,
  user_email: str = Query(..., min_length=3),
  limit: int = Query(20, ge=1, le=100),
  cursor: int = Query(0, ge=0),
):
  conversations_collection = _get_conversations_collection()
  chat_collection = _get_chat_messages_collection()

  if conversations_collection is None or chat_collection is None:
    raise HTTPException(status_code=500, detail="Conversation stores are unavailable")

  conversation = conversations_collection.find_one({"conversationId": conversation_id})
  if conversation is None:
    raise HTTPException(status_code=404, detail="Conversation not found")

  if conversation.get("userEmail") != user_email:
    raise HTTPException(status_code=403, detail="Forbidden for this conversation")

  message_count = int(conversation.get("messageCount", 0))#get the total number of messages in the conversation from the conversation metadata
  raw_docs = list(
    chat_collection.find(
      {"conversationId": conversation_id},
      sort={"timestamp": -1},
      skip=cursor,
      limit=limit + 1,
    )
  )

  # Determine if there are more messages to load beyond the current page
  has_more = len(raw_docs) > limit
  paged_docs = raw_docs[:limit]
  paged_docs.reverse()#reverse the order of the messages to be chronological (oldest to newest) before returning to the frontend
  next_cursor = cursor + limit if has_more else None#calculate the next cursor value for pagination 
  #if there are more messages to load, the next cursor will be the current cursor plus the limit; 
  #if there are no more messages, the next cursor will be null

  return ConversationMessagesResponse(#return the conversation metadata and the list of messages in the response model format, along with pagination info
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
        userEmail=doc.get("userEmail"),
      )
      for doc in paged_docs
    ],
    hasMore=has_more,
    nextCursor=next_cursor,
  )


@router.patch("/conversations/{conversation_id}/title", response_model=RenameConversationResponse)#endpoint to rename a conversation
def rename_conversation(#path parameter for conversation ID; query parameter for user email; request body with new title
  conversation_id: str,
  user_email: str = Query(..., min_length=3),
  request: RenameConversationRequest = None,
):
  if request is None:
    raise HTTPException(status_code=400, detail="Request body required")
  
  conversations_collection = _get_conversations_collection()
  if conversations_collection is None:
    raise HTTPException(status_code=500, detail="Conversations store is unavailable")

  conversation = conversations_collection.find_one({"conversationId": conversation_id})
  if conversation is None:
    raise HTTPException(status_code=404, detail="Conversation not found")

  if conversation.get("userEmail") != user_email:
    raise HTTPException(status_code=403, detail="Forbidden for this conversation")

  from datetime import datetime, timezone
  timestamp = datetime.now(timezone.utc).isoformat()

  update_result = conversations_collection.update_one(
    {"conversationId": conversation_id},
    {"$set": {"title": request.title.strip(), "updatedAt": timestamp}}
  )

  if update_result is None:
    raise HTTPException(status_code=500, detail="Failed to update conversation")

  return RenameConversationResponse(
    conversationId=conversation_id,
    title=request.title.strip(),
    updatedAt=timestamp,
  )