#!/usr/bin/env python3
"""
Diagnosis script to test the modifications service.
Run this from the backend directory to identify issues.
"""

import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

async def test_vectordb_connection():
    """Test if Vector Store and Parent Store are accessible."""
    print("\n=== Testing Vector Database Connection ===")
    
    try:
        from app.vectordb.vectordb import VECTOR_STORE, PARENT_STORE
        print("✓ Successfully imported VECTOR_STORE and PARENT_STORE")
        
        # Test if stores exist
        if VECTOR_STORE is None:
            print("✗ VECTOR_STORE is None - initialization may have failed")
            return False
        if PARENT_STORE is None:
            print("✗ PARENT_STORE is None - initialization may have failed")
            return False
            
        print("✓ Both stores are initialized")
        return True
        
    except Exception as e:
        print(f"✗ Error importing stores: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_vector_search():
    """Test if we can query the Vector Store."""
    print("\n=== Testing Vector Store Search ===")
    
    try:
        from app.vectordb.vectordb import VECTOR_STORE
        
        print("  → Attempting search with 'document'...")
        results = await VECTOR_STORE.asimilarity_search("document", k=5)
        print(f"  ✓ Search succeeded - found {len(results)} results")
        
        if len(results) > 0:
            print("    Sample result:")
            doc, score = results[0]
            print(f"    - Content: {doc.page_content[:100]}...")
            print(f"    - Parent ID: {doc.metadata.get('parent_id')}")
            print(f"    - Score: {score}")
        else:
            print("  ! No documents found - have you uploaded any files?")
            
        return len(results) > 0
        
    except Exception as e:
        print(f"✗ Vector search failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_parent_store():
    """Test if we can query the Parent Store."""
    print("\n=== Testing Parent Store ===")
    
    try:
        from app.vectordb.vectordb import VECTOR_STORE, PARENT_STORE
        
        # First get some parent IDs from Vector Store
        print("  → Getting sample documents from Vector Store...")
        results = await VECTOR_STORE.asimilarity_search("document", k=3)
        
        if len(results) == 0:
            print("  ! No documents in Vector Store - cannot test Parent Store")
            return False
        
        parent_ids = list(set(doc.metadata.get("parent_id") for doc, _ in results if doc.metadata.get("parent_id")))
        print(f"  ✓ Found {len(parent_ids)} unique parent IDs")
        
        if len(parent_ids) > 0:
            print(f"  → Querying Parent Store for {len(parent_ids)} documents...")
            parent_docs = await PARENT_STORE.amget(parent_ids)
            print(f"  ✓ Parent Store query succeeded - got {len(parent_docs)} results")
            
            for idx, pdoc in enumerate(parent_docs[:2]):
                if pdoc:
                    content = pdoc.get("page_content", "") if isinstance(pdoc, dict) else getattr(pdoc, "page_content", "")
                    print(f"    Doc {idx}: {len(content)} chars")
                else:
                    print(f"    Doc {idx}: None/empty")
            
            return len(parent_docs) > 0
        
        return False
        
    except Exception as e:
        print(f"✗ Parent Store test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_reconstruction_service():
    """Test the ReconstructionService directly."""
    print("\n=== Testing ReconstructionService ===")
    
    try:
        from app.service.modification.reconstruction_service import ReconstructionService
        
        print("  → Calling get_all_documents()...")
        documents = await ReconstructionService.get_all_documents()
        print(f"  ✓ ReconstructionService succeeded - returned {len(documents)} documents")
        
        for doc in documents[:2]:
            print(f"    - {doc['fileName']}: {doc['size']} chars ({doc['chunks']} chunks)")
        
        return len(documents) > 0
        
    except Exception as e:
        print(f"✗ ReconstructionService test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all diagnostic tests."""
    print("\n" + "="*50)
    print("  MODIFICATION SERVICE DIAGNOSTIC")
    print("="*50)
    
    results = {
        "Vector DB Connection": await test_vectordb_connection(),
        "Vector Search": await test_vector_search(),
        "Parent Store": await test_parent_store(),
        "ReconstructionService": await test_reconstruction_service(),
    }
    
    print("\n" + "="*50)
    print("  DIAGNOSTIC SUMMARY")
    print("="*50)
    
    for test_name, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    print("\n" + "="*50)
    
    # Provide recommendations
    if not results["Vector DB Connection"]:
        print("\n⚠️  RECOMMENDATIONS:")
        print("1. Check that your .env file has ASTRA_DB_URL and ASTRA_DB_TOKEN")
        print("2. Verify the backend was started after .env was configured")
        print("3. Restart the backend: uvicorn app.main:app --reload")
    
    if not results["Vector Search"]:
        print("\n⚠️  RECOMMENDATIONS:")
        print("1. Have you uploaded any documents yet?")
        print("2. Upload a document through the frontend first")
        print("3. Wait a few seconds for processing to complete")
    
    if not results["ReconstructionService"]:
        print("\n⚠️  Check the error messages above for specific issues")
    
    all_passed = all(results.values())
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
