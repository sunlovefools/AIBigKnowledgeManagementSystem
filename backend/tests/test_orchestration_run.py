"""
Quick Integration Test for RAG Orchestration Layer
-------------------------------------------------
This script checks end-to-end functionality:
1. LLM connection (Beam)
2. Embedding service connection
3. AstraDB vector retrieval
4. RAG pipeline orchestration
"""

import asyncio
from app.service.mock_beam_client import check_service_health, query_llm, get_embedding
from app.service.vector_store import get_vector_store
from app.service.orchestration import get_orchestrator


async def main():
    print("\n==============================")
    print("🧪 Starting Orchestration Layer Test")
    print("==============================\n")

    # -----------------------------
    # 1. Check Beam Service Health
    # -----------------------------
    print("🔹 Checking Beam endpoints...")
    health = check_service_health()
    for name, status in health.items():
        print(f"   {name} → {status}")
    print()

    # -----------------------------
    # 2. Test LLM basic response
    # -----------------------------
    print("🔹 Testing LLM basic prompt...")
    llm_output = query_llm("Briefly explain what Retrieval-Augmented Generation (RAG) means.", max_new_tokens=80)
    print(f"✅ LLM output:\n{llm_output}\n")

    # -----------------------------
    # 3. Test Embedding service
    # -----------------------------
    print("🔹 Getting embedding for test query...")
    embedding = get_embedding("What is LangChain used for?")
    if embedding and len(embedding) > 10:
        print(f"✅ Embedding length: {len(embedding)}\n")
    else:
        print("❌ Embedding service failed or returned empty list\n")

    # -----------------------------
    # 4. Test Vector Store Connection
    # -----------------------------
    print("🔹 Connecting to AstraDB collection...")
    try:
        vector_store = get_vector_store()
        print(f"✅ Connected to: {vector_store.collection_name}\n")

        # Optional: try counting documents
        user_id = "test_user"
        count = vector_store.get_user_document_count(user_id)
        print(f"📚 Found {count} chunks for user '{user_id}'\n")

    except Exception as e:
        print(f"❌ Vector Store connection error: {e}\n")

    # -----------------------------
    # 5. Test Full RAG Pipeline
    # -----------------------------
    print("🔹 Testing full RAG orchestrator pipeline...\n")

    orchestrator = get_orchestrator()
    query = "Explain what LangChain does in AI applications."

    result = orchestrator.process_query_with_rag(user_query=query, user_id="test_user")
    print("✅ RAG Pipeline Result:")
    print(f"Answer:\n{result['answer']}\n")
    print(f"Refined Query: {result['refined_query']}")
    print(f"Sources Used: {len(result['sources'])}")
    for src in result['sources']:
        print(f" - {src['source']} (similarity: {src['similarity_score']:.3f})")

    print("\n==============================")
    print("✅ All tests completed")
    print("==============================\n")


if __name__ == "__main__":
    asyncio.run(main())
