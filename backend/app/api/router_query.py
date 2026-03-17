import os
from datetime import datetime, timezone
from uuid import uuid4

from astrapy import DataAPIClient
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# Local imports
from app.service.rag.retrieval.query_refiner import refine_query
from app.vectordb.vectordb import search_and_retrieve_context
from app.service.rag.retrieval.answer_generator import generate_answer

# Setup the API router
router = APIRouter()

_CHAT_COLLECTION = None
_CONVERSATIONS_COLLECTION = None


def _get_chat_database():
    # Try chat-specific database first, fallback to main database.
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
            print("⚠️ Chat collection disabled: Missing ASTRA DB credentials")
            return None

        _CHAT_COLLECTION = database.get_collection(chat_collection_name)
        print(f"✅ Chat collection '{chat_collection_name}' connected")
        return _CHAT_COLLECTION
    except Exception as e:
        print(f"⚠️ Chat collection init failed: {e}")
        return None


def _get_conversations_collection():
    global _CONVERSATIONS_COLLECTION
    if _CONVERSATIONS_COLLECTION is not None:
        return _CONVERSATIONS_COLLECTION

    conversations_collection_name = os.getenv("ASTRA_CONVERSATIONS_COLLECTION", "conversations")

    try:
        database = _get_chat_database()
        if database is None:
            print("⚠️ Conversations collection disabled: Missing ASTRA DB credentials")
            return None

        _CONVERSATIONS_COLLECTION = database.get_collection(conversations_collection_name)
        print(f"✅ Conversations collection '{conversations_collection_name}' connected")
        return _CONVERSATIONS_COLLECTION
    except Exception as e:
        print(f"⚠️ Conversations collection init failed: {e}")
        return None

# Enforce limit on messages per conversation (max 100 messages, oldest first)
def _enforce_message_limit(conversation_id: str, max_messages: int = 100):
    """Delete oldest messages if conversation exceeds max_messages limit."""
    chat_collection = _get_chat_collection()
    conversations_collection = _get_conversations_collection()
    
    if chat_collection is None or conversations_collection is None:
        return
    
    try:
        # Count messages in this conversation
        count = chat_collection.count_documents({"conversationId": conversation_id})
        
        if count > max_messages:
            # Find the oldest message by timestamp
            oldest_messages = list(
                chat_collection.find(
                    {"conversationId": conversation_id},
                    sort={"timestamp": 1},
                    limit=count - max_messages,  # Delete enough messages to get back to max_messages
                )
            )
            
            # Delete the oldest messages
            for msg in oldest_messages:
                chat_collection.delete_one({"_id": msg.get("_id")})
            
            # Update message count in conversation metadata
            conversations_collection.update_one(
                {"conversationId": conversation_id},
                {"$inc": {"messageCount": -(count - max_messages)}}
            )
            print(f"Replaced {count - max_messages} oldest messages from conversation {conversation_id}")
    except Exception as e:
        print(f"!!!ERROR:Failed to enforce message limit: {e}")

# Enforce limit on conversations per user (max 20 conversations, oldest first)
def _enforce_conversation_limit(user_email: str, max_conversations: int = 20):
    """Delete oldest conversation if user exceeds max_conversations limit."""
    chat_collection = _get_chat_collection()
    conversations_collection = _get_conversations_collection()
    
    if conversations_collection is None:
        return
    
    try:
        # Count conversations for this user
        count = conversations_collection.count_documents({"userEmail": user_email})
        
        if count > max_conversations:
            # Find the oldest conversation by createdAt timestamp
            oldest_conversations = list(
                conversations_collection.find(
                    {"userEmail": user_email},
                    sort={"createdAt": 1},
                    limit=count - max_conversations,  # Delete enough to get back to max_conversations
                )
            )
            
            # Delete the oldest conversations and their messages
            for conv in oldest_conversations:
                old_conv_id = conv.get("conversationId")
                
                # Delete conversation metadata
                conversations_collection.delete_one({"_id": conv.get("_id")})
                
                # Delete all messages in that conversation
                if chat_collection and old_conv_id:
                    chat_collection.delete_many({"conversationId": old_conv_id})
                
                print(f"Deleted oldest conversation {old_conv_id} for user {user_email}")
    except Exception as e:
        print(f"!!!ERROR: Failed to enforce conversation limit: {e}")

#Generates conversation title based on the first user message, with a max length and ellipsis if truncated.
def _generate_conversation_title(first_user_message: str):
    normalized = " ".join(first_user_message.strip().split())
    if not normalized:
        return "New conversation"

    max_length = 60
    return normalized[:max_length].rstrip() + ("..." if len(normalized) > max_length else "")

#Add/update conversation metadata (title, timestamps, message count) whenever a new message is saved.
def _upsert_conversation_metadata(
    conversation_id: str,
    user_email: str,
    role: str,
    text: str,
):
    collection = _get_conversations_collection()#returns AstraDB conversations collection
    if collection is None:#if DB not been initialized
        return None

    timestamp = datetime.now(timezone.utc).isoformat()
    existing = collection.find_one({"conversationId": conversation_id})#check if conversation metadata already exists for this conversationId

    if existing:#update existing conversation metadata
        current_count = int(existing.get("messageCount", 0))
        existing_title = existing.get("title", "New conversation")
        should_replace_title = role == "user" and (not existing_title or existing_title == "New conversation")
        next_title = _generate_conversation_title(text) if should_replace_title else existing_title

        update_result = collection.update_one(
            {"conversationId": conversation_id},
            {
                "$set": {
                    "userEmail": user_email,
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

    #if no existing metadata, create new metadata entry for this conversation
    title = _generate_conversation_title(text) if role == "user" else "New conversation"
    conversationDocument = {
        "conversationId": conversation_id,
        "userEmail": user_email,
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
    collection.insert_one(conversationDocument)
    return {
        "conversationId": conversation_id,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "messageCount": 1,
        "title": title,
        "created": True,
    }

# Save each chat message to the 'chat_messages' collection, and update conversation metadata in 'conversations' collection.
def _save_chat_message(conversation_id: str, user_email: str, role: str, text: str):
    collection = _get_chat_collection()
    if collection is None:
        return None

    timestamp = datetime.now(timezone.utc).isoformat()
    chatDocument = {#create a document for chat message with all metadata
        "conversationId": conversation_id, 
        "userEmail": user_email,
        "role": role,
        "text": text,
        "timestamp": timestamp,
    }

    result = collection.insert_one(chatDocument)#returns ID of newly inserted chat message document
    
    is_new_conversation = False
    try:
        metadata_result = _upsert_conversation_metadata(#update conversation of chat message with new metadata e.g. lastMessage and timestamp will have changed
            conversation_id=conversation_id,
            user_email=user_email,
            role=role,
            text=text,
        )
        is_new_conversation = metadata_result and metadata_result.get("created", False)
    except Exception as e:#if conversation metadata update fails, log the error but do not fail the entire message saving process
        print(f"Failed to update conversation's metadata: {e}")
    
    # Message limit (max 100 messages per conversation)
    try:
        _enforce_message_limit(conversation_id)
    except Exception as e:
        print(f"⚠️ Failed to enforce message limit: {e}")
    
    # Conversation limit (max 20 conversations per user) when creating new conversation
    if is_new_conversation:
        try:
            _enforce_conversation_limit(user_email)
        except Exception as e:
            print(f"⚠️ Failed to enforce conversation limit: {e}")

    return {#return the saved chat message with its new ID and metadata
        "messageId": str(result.inserted_id),
        "conversationId": conversation_id,
        "userEmail": user_email,
        "role": role,
        "text": text,
        "timestamp": timestamp,
    }

# --- Data Models ---
class QueryRequest(BaseModel):
    """
    Request model for RAG query endpoint.
    """
    query: str
    top_k: int = 10  # Number of similar child documents to retrieve
    conversation_id: str | None = None
    user_email: str | None = None

class QueryResponse(BaseModel):
    """
    Response model for RAG query endpoint.
    """
    answer: str
    conversation_id: str
    saved_messages: list[dict] = Field(default_factory=list)


# --- query/ endpoint ---

@router.get("/health")
def query_health():
    """
    Health check endpoint for query endpoint.
    """
    return {"query_service": "ok"}

@router.post("/query", response_model=QueryResponse)
async def query_documents(request: QueryRequest):
    """
    Full RAG Query Pipeline using the Parent-Child Retriever pattern:
    1. Refine the user query using LLM. (It is currently skipped as experiment)
    2. Search for relevant child chunks and retrieve associated parent conversationDocument contents (LangChain/AstraDB).
    3. Generate an answer from the context using the Answer Generator LLM.
    """

    conversation_id = request.conversation_id or str(uuid4())
    user_email = request.user_email or "anonymous@local"

    saved_messages: list[dict] = []

    try:
        saved_user_message = _save_chat_message(
            conversation_id=conversation_id,
            user_email=user_email,
            role="user",
            text=request.query,
        )
        if saved_user_message is not None:
            saved_messages.append(saved_user_message)
    except Exception as e:
        print(f"⚠️ Failed to persist user chat message: {e}")

    # --- Step 1: Retrieval of Parent Documents (Full Context) ---
    try:
        # search_and_retrieve_context performs vector search on child chunks 
        # and looks up the full content from the parent documents.
        rag_docs = await search_and_retrieve_context(
            query=request.query,
            top_k=request.top_k
        )

        if not rag_docs:
            no_docs_answer = "No relevant documents found for your query. Try ingesting more data."
            try:
                saved_ai_message = _save_chat_message(
                    conversation_id=conversation_id,
                    user_email=user_email,
                    role="ai",
                    text=no_docs_answer,
                )
                if saved_ai_message is not None:
                    saved_messages.append(saved_ai_message)
            except Exception as e:
                print(f"⚠️ Failed to persist AI chat message: {e}")

            return QueryResponse(
                answer=no_docs_answer,
                conversation_id=conversation_id,
                saved_messages=saved_messages,
            )

    except Exception as e:
        print(f"❌ Retrieval failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Context retrieval failed: {str(e)}"
        )


    # ---- Step 2: Send to LLM for Answer Generation ----
    try:
        answer = await generate_answer(rag_docs, request.query)
        print("🧠 LLM Answer Generated!")
    except Exception as error:
        print(f"❌ LLM Answer Generation Failed: {error}")
        raise HTTPException(
            status_code=500,
            detail=f"LLM answer generation failed: {str(error)}"
        )

    try:
        saved_ai_message = _save_chat_message(
            conversation_id=conversation_id,
            user_email=user_email,
            role="ai",
            text=answer,
        )
        if saved_ai_message is not None:
            saved_messages.append(saved_ai_message)
    except Exception as e:
        print(f"⚠️ Failed to persist AI chat message: {e}")

    print("✅ Query Process Completed Successfully")
    return QueryResponse(
        answer=answer,
        conversation_id=conversation_id,
        saved_messages=saved_messages,
    )
