"""
Integration tests for query module
Tests the complete RAG query pipeline end-to-end
"""
import sys
import asyncio
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parents[2]   # 到 backend/
sys.path.insert(0, str(BACKEND_DIR))

from app.api.router_query import query_documents, query_documents_direct, QueryRequest


async def test_query_pipeline_basic():
    """Test complete query pipeline with basic English query"""
    print("=== Test 1: Basic Query Pipeline ===\n")
    
    request = QueryRequest(
        query="What is machine learning?",
        top_k=3
    )
    
    try:
        result = await query_documents(request)
        
        assert result is not None, "Result should not be None"
        assert hasattr(result, 'answer'), "Result should have answer attribute"
        assert len(result.answer) > 0, "Answer should not be empty"
        
        print(f"✅ Query: '{request.query}'")
        print(f"✅ Top K: {request.top_k}")
        print(f"✅ Answer: '{result.answer[:200]}...'")
        print(f"✅ Pipeline completed successfully\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_query_pipeline_chinese():
    """Test query pipeline with Chinese query"""
    print("=== Test 2: Chinese Query Pipeline ===\n")
    
    request = QueryRequest(
        query="什么是深度学习？",
        top_k=3
    )
    
    try:
        result = await query_documents(request)
        
        assert result is not None, "Result should not be None"
        assert hasattr(result, 'answer'), "Result should have answer attribute"
        assert len(result.answer) > 0, "Answer should not be empty"
        
        print(f"✅ Query: '{request.query}'")
        print(f"✅ Answer: '{result.answer[:200]}...'")
        print(f"✅ Chinese query handled successfully\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_query_pipeline_different_top_k():
    """Test query pipeline with different top_k values"""
    print("=== Test 3: Different Top K Values ===\n")
    
    query = "Explain neural networks"
    top_k_values = [1, 3, 5, 10]
    
    for top_k in top_k_values:
        request = QueryRequest(query=query, top_k=top_k)
        
        try:
            result = await query_documents(request)
            
            assert result is not None, f"Result should not be None for top_k={top_k}"
            assert hasattr(result, 'answer'), f"Result should have answer for top_k={top_k}"
            
            print(f"✅ top_k={top_k}: Answer generated ({len(result.answer)} chars)")
        except Exception as e:
            print(f"❌ top_k={top_k} failed: {e}")
            return False
    
    print(f"✅ All top_k values handled successfully\n")
    return True


async def test_query_direct_endpoint():
    """Test direct query endpoint (without refinement)"""
    print("=== Test 4: Direct Query Endpoint ===\n")
    
    request = QueryRequest(
        query="What is Python programming?",
        top_k=3
    )
    
    try:
        result = await query_documents_direct(request)
        
        assert result is not None, "Result should not be None"
        
        print(f"✅ Direct query: '{request.query}'")
        print(f"✅ Direct endpoint works correctly\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_query_long_query():
    """Test query pipeline with long query"""
    print("=== Test 5: Long Query Pipeline ===\n")
    
    long_query = "I want to understand the fundamental concepts of artificial intelligence, " \
                 "including machine learning, deep learning, neural networks, and how they " \
                 "are applied in real-world applications like image recognition and natural " \
                 "language processing. Can you explain all of these?"
    
    request = QueryRequest(
        query=long_query,
        top_k=5
    )
    
    try:
        result = await query_documents(request)
        
        assert result is not None, "Result should not be None"
        assert hasattr(result, 'answer'), "Result should have answer attribute"
        assert len(result.answer) > 0, "Answer should not be empty"
        
        print(f"✅ Long query length: {len(long_query)} characters")
        print(f"✅ Answer: '{result.answer[:200]}...'")
        print(f"✅ Long query handled successfully\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_query_special_characters():
    """Test query pipeline with special characters"""
    print("=== Test 6: Special Characters Query ===\n")
    
    request = QueryRequest(
        query="What's the difference between Python 3.9 & 3.10? @#$",
        top_k=3
    )
    
    try:
        result = await query_documents(request)
        
        assert result is not None, "Result should not be None"
        assert hasattr(result, 'answer'), "Result should have answer attribute"
        
        print(f"✅ Query with special chars: '{request.query}'")
        print(f"✅ Answer: '{result.answer[:200]}...'")
        print(f"✅ Special characters handled successfully\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_query_technical_terms():
    """Test query pipeline with technical terminology"""
    print("=== Test 7: Technical Terms Query ===\n")
    
    request = QueryRequest(
        query="Explain backpropagation in CNN with ReLU activation function",
        top_k=5
    )
    
    try:
        result = await query_documents(request)
        
        assert result is not None, "Result should not be None"
        assert hasattr(result, 'answer'), "Result should have answer attribute"
        assert len(result.answer) > 0, "Answer should not be empty"
        
        print(f"✅ Technical query: '{request.query}'")
        print(f"✅ Answer: '{result.answer[:200]}...'")
        print(f"✅ Technical terms handled successfully\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_query_mixed_language():
    """Test query pipeline with mixed language query"""
    print("=== Test 8: Mixed Language Query ===\n")
    
    request = QueryRequest(
        query="How does 机器学习 machine learning 工作?",
        top_k=3
    )
    
    try:
        result = await query_documents(request)
        
        assert result is not None, "Result should not be None"
        assert hasattr(result, 'answer'), "Result should have answer attribute"
        
        print(f"✅ Mixed language query: '{request.query}'")
        print(f"✅ Answer: '{result.answer[:200]}...'")
        print(f"✅ Mixed language handled successfully\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def run_all_tests():
    """Run all query integration tests"""
    print("\n" + "="*60)
    print("        QUERY MODULE INTEGRATION TESTING")
    print("="*60 + "\n")
    
    tests = [
        test_query_pipeline_basic,
        test_query_pipeline_chinese,
        test_query_pipeline_different_top_k,
        test_query_direct_endpoint,
        test_query_long_query,
        test_query_special_characters,
        test_query_technical_terms,
        test_query_mixed_language,
    ]
    
    passed = 0
    failed = 0
    
    for test_func in tests:
        try:
            result = await test_func()
            if result:
                passed += 1
            else:
                failed += 1
        except AssertionError as e:
            print(f"❌ {test_func.__name__} failed: {e}\n")
            failed += 1
        except Exception as e:
            print(f"❌ {test_func.__name__} error: {e}\n")
            failed += 1
    
    # Summary
    print("="*60)
    print("        TEST SUMMARY")
    print("="*60)
    print(f"✅ Passed:  {passed}")
    print(f"❌ Failed:  {failed}")
    print(f"Total:     {passed + failed}")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(run_all_tests())