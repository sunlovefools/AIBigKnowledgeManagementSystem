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


def _generate_conversation_title(first_user_message: str):
    # Keep title deterministic and lightweight for now.
    normalized = " ".join(first_user_message.strip().split())
    if not normalized:
        return "New conversation"

    max_length = 60
    return normalized[:max_length].rstrip() + ("..." if len(normalized) > max_length else "")


def _upsert_conversation_metadata(
    conversation_id: str,
    user_email: str,
    role: str,
    text: str,
):
    collection = _get_conversations_collection()
    if collection is None:
        return None

    timestamp = datetime.now(timezone.utc).isoformat()
    existing = collection.find_one({"conversationId": conversation_id})

    if existing:
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

    title = _generate_conversation_title(text) if role == "user" else "New conversation"
    document = {
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
    collection.insert_one(document)
    return {
        "conversationId": conversation_id,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "messageCount": 1,
        "title": title,
        "created": True,
    }


def _save_chat_message(conversation_id: str, user_email: str, role: str, text: str):
    collection = _get_chat_collection()
    if collection is None:
        return None

    timestamp = datetime.now(timezone.utc).isoformat()
    document = {
        "conversationId": conversation_id,
        "userEmail": user_email,
        "role": role,
        "text": text,
        "timestamp": timestamp,
    }

    result = collection.insert_one(document)
    try:
        _upsert_conversation_metadata(
            conversation_id=conversation_id,
            user_email=user_email,
            role=role,
            text=text,
        )
    except Exception as e:
        print(f"⚠️ Failed to update conversation metadata: {e}")

    return {
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
    2. Search for relevant child chunks and retrieve associated parent document contents (LangChain/AstraDB).
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
