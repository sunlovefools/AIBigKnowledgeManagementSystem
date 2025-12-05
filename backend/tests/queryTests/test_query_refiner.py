"""
Unit tests for query_refiner module
Tests query refinement functionality using Beam LLM API
"""
import os
print("DEBUG:", os.getenv("BEAM_REFINE_LLM_URL"))
print("DEBUG:", os.getenv("BEAM_REFINE_LLM_KEY"))
import sys
import asyncio
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]   # 到 backend/
sys.path.insert(0, str(BACKEND_DIR))

from app.service.query_refiner import refine_query


async def test_refine_query_basic():
    """Test basic query refinement with simple English query"""
    print("=== Test 1: Basic Query Refinement ===\n")
    
    query = "What is machine learning?"
    
    try:
        result = await refine_query(query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        assert len(result) > 0, "Refined query should not be empty"
        
        print(f"✅ Original query: '{query}'")
        print(f"✅ Refined query: '{result}'")
        print(f"✅ Result length: {len(result)} characters\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_refine_query_chinese():
    """Test query refinement with Chinese query"""
    print("=== Test 2: Chinese Query Refinement ===\n")
    
    query = "什么是深度学习？"
    
    try:
        result = await refine_query(query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        assert len(result) > 0, "Refined query should not be empty"
        
        print(f"✅ Original query: '{query}'")
        print(f"✅ Refined query: '{result}'")
        print(f"✅ Chinese query handled successfully\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_refine_query_long():
    """Test query refinement with a longer, complex query"""
    print("=== Test 3: Long Query Refinement ===\n")
    
    query = "I want to understand how neural networks work and what are the differences between CNN and RNN and when should I use each one in my projects"
    
    try:
        result = await refine_query(query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        assert len(result) > 0, "Refined query should not be empty"
        
        print(f"✅ Original query: '{query}'")
        print(f"✅ Original length: {len(query)} characters")
        print(f"✅ Refined query: '{result}'")
        print(f"✅ Refined length: {len(result)} characters\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_refine_query_special_characters():
    """Test query refinement with special characters"""
    print("=== Test 4: Special Characters Query ===\n")
    
    query = "What is the difference between Python 3.9 & 3.10? @#$%"
    
    try:
        result = await refine_query(query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        
        print(f"✅ Original query: '{query}'")
        print(f"✅ Refined query: '{result}'")
        print(f"✅ Special characters handled\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_refine_query_short():
    """Test query refinement with very short query"""
    print("=== Test 5: Short Query Refinement ===\n")
    
    query = "AI?"
    
    try:
        result = await refine_query(query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        
        print(f"✅ Original query: '{query}'")
        print(f"✅ Refined query: '{result}'")
        print(f"✅ Short query handled\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_refine_query_mixed_language():
    """Test query refinement with mixed language query"""
    print("=== Test 6: Mixed Language Query ===\n")
    
    query = "How does machine learning 机器学习 work?"
    
    try:
        result = await refine_query(query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        assert len(result) > 0, "Refined query should not be empty"
        
        print(f"✅ Original query: '{query}'")
        print(f"✅ Refined query: '{result}'")
        print(f"✅ Mixed language handled\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_refine_query_technical():
    """Test query refinement with technical terminology"""
    print("=== Test 7: Technical Query Refinement ===\n")
    
    query = "Explain backpropagation in CNN with ReLU activation"
    
    try:
        result = await refine_query(query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        assert len(result) > 0, "Refined query should not be empty"
        
        print(f"✅ Original query: '{query}'")
        print(f"✅ Refined query: '{result}'")
        print(f"✅ Technical terms handled\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def run_all_tests():
    """Run all query refiner tests"""
    print("\n" + "="*60)
    print("     QUERY REFINER MODULE TESTING")
    print("="*60 + "\n")
    
    tests = [
        test_refine_query_basic,
        test_refine_query_chinese,
        test_refine_query_long,
        test_refine_query_special_characters,
        test_refine_query_short,
        test_refine_query_mixed_language,
        test_refine_query_technical,
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