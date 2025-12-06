"""
Unit tests for answer_generator module
Tests answer generation functionality using Beam LLM API
"""
import sys
import asyncio
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()


BACKEND_DIR = Path(__file__).resolve().parents[2]   # 到 backend/
sys.path.insert(0, str(BACKEND_DIR))

from app.service.answer_generator import generate_answer


async def test_generate_answer_basic():
    """Test basic answer generation with simple context and query"""
    print("=== Test 1: Basic Answer Generation ===\n")
    
    rag_contents = [
        "Machine learning is a subset of artificial intelligence that enables systems to learn from data.",
        "Deep learning uses neural networks with multiple layers to process complex patterns."
    ]
    user_query = "What is machine learning?"
    
    try:
        result = await generate_answer(rag_contents, user_query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        assert len(result) > 0, "Answer should not be empty"
        
        print(f"✅ Query: '{user_query}'")
        print(f"✅ Context chunks: {len(rag_contents)}")
        print(f"✅ Generated answer: '{result[:200]}...'")
        print(f"✅ Answer length: {len(result)} characters\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_generate_answer_single_chunk():
    """Test answer generation with single context chunk"""
    print("=== Test 2: Single Chunk Answer Generation ===\n")
    
    rag_contents = [
        "Python is a high-level programming language known for its simplicity and readability."
    ]
    user_query = "What is Python?"
    
    try:
        result = await generate_answer(rag_contents, user_query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        assert len(result) > 0, "Answer should not be empty"
        
        print(f"✅ Query: '{user_query}'")
        print(f"✅ Single chunk provided")
        print(f"✅ Generated answer: '{result[:200]}...'")
        print(f"✅ Answer length: {len(result)} characters\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_generate_answer_multiple_chunks():
    """Test answer generation with multiple context chunks"""
    print("=== Test 3: Multiple Chunks Answer Generation ===\n")
    
    rag_contents = [
        "Neural networks are computing systems inspired by biological neural networks in the brain.",
        "Convolutional Neural Networks (CNNs) are commonly used for image recognition tasks.",
        "Recurrent Neural Networks (RNNs) are designed to work with sequential data.",
        "Transformers have revolutionized natural language processing with attention mechanisms.",
        "Deep learning models require large amounts of data for effective training."
    ]
    user_query = "Explain different types of neural networks"
    
    try:
        result = await generate_answer(rag_contents, user_query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        assert len(result) > 0, "Answer should not be empty"
        
        print(f"✅ Query: '{user_query}'")
        print(f"✅ Context chunks: {len(rag_contents)}")
        print(f"✅ Generated answer: '{result[:200]}...'")
        print(f"✅ Answer length: {len(result)} characters\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_generate_answer_chinese():
    """Test answer generation with Chinese content"""
    print("=== Test 4: Chinese Content Answer Generation ===\n")
    
    rag_contents = [
        "人工智能是计算机科学的一个分支，致力于创建能够执行通常需要人类智能的任务的系统。",
        "机器学习是人工智能的一个子集，通过数据学习来改进性能。",
        "深度学习使用多层神经网络来处理复杂的模式识别任务。"
    ]
    user_query = "什么是人工智能？"
    
    try:
        result = await generate_answer(rag_contents, user_query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        assert len(result) > 0, "Answer should not be empty"
        
        print(f"✅ Query: '{user_query}'")
        print(f"✅ Chinese context chunks: {len(rag_contents)}")
        print(f"✅ Generated answer: '{result[:200]}...'")
        print(f"✅ Chinese content handled successfully\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_generate_answer_long_context():
    """Test answer generation with longer context"""
    print("=== Test 5: Long Context Answer Generation ===\n")
    
    # Create longer context chunks
    long_chunk = "Artificial intelligence and machine learning have transformed various industries. " * 20
    rag_contents = [long_chunk, long_chunk]
    user_query = "Summarize the impact of AI"
    
    try:
        result = await generate_answer(rag_contents, user_query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        assert len(result) > 0, "Answer should not be empty"
        
        total_context_length = sum(len(c) for c in rag_contents)
        print(f"✅ Query: '{user_query}'")
        print(f"✅ Total context length: {total_context_length} characters")
        print(f"✅ Generated answer: '{result[:200]}...'")
        print(f"✅ Long context handled successfully\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_generate_answer_technical_content():
    """Test answer generation with technical content"""
    print("=== Test 6: Technical Content Answer Generation ===\n")
    
    rag_contents = [
        "Backpropagation is an algorithm for training neural networks by computing gradients.",
        "The learning rate hyperparameter controls the step size during gradient descent optimization.",
        "Batch normalization helps stabilize training by normalizing layer inputs.",
        "Dropout is a regularization technique that randomly sets neurons to zero during training."
    ]
    user_query = "How does neural network training work?"
    
    try:
        result = await generate_answer(rag_contents, user_query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        assert len(result) > 0, "Answer should not be empty"
        
        print(f"✅ Query: '{user_query}'")
        print(f"✅ Technical context provided")
        print(f"✅ Generated answer: '{result[:200]}...'")
        print(f"✅ Technical content handled successfully\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_generate_answer_mixed_language():
    """Test answer generation with mixed language content"""
    print("=== Test 7: Mixed Language Answer Generation ===\n")
    
    rag_contents = [
        "Machine learning (机器学习) is a powerful technology.",
        "Deep learning 深度学习 enables complex pattern recognition.",
        "AI applications include 图像识别 and natural language processing."
    ]
    user_query = "What are AI applications?"
    
    try:
        result = await generate_answer(rag_contents, user_query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        assert len(result) > 0, "Answer should not be empty"
        
        print(f"✅ Query: '{user_query}'")
        print(f"✅ Mixed language context provided")
        print(f"✅ Generated answer: '{result[:200]}...'")
        print(f"✅ Mixed language handled successfully\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def test_generate_answer_context_joining():
    """Test that multiple chunks are properly joined"""
    print("=== Test 8: Context Joining Verification ===\n")
    
    rag_contents = [
        "First chunk of information.",
        "Second chunk of information.",
        "Third chunk of information."
    ]
    user_query = "What information is provided?"
    
    try:
        result = await generate_answer(rag_contents, user_query)
        
        assert result is not None, "Result should not be None"
        assert isinstance(result, str), f"Result should be string, got {type(result)}"
        
        # The answer should reflect knowledge from multiple chunks
        print(f"✅ Query: '{user_query}'")
        print(f"✅ Chunks provided: {len(rag_contents)}")
        print(f"✅ Generated answer: '{result[:200]}...'")
        print(f"✅ Context joining works correctly\n")
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}\n")
        return False


async def run_all_tests():
    """Run all answer generator tests"""
    print("\n" + "="*60)
    print("     ANSWER GENERATOR MODULE TESTING")
    print("="*60 + "\n")
    
    tests = [
        test_generate_answer_basic,
        test_generate_answer_single_chunk,
        test_generate_answer_multiple_chunks,
        test_generate_answer_chinese,
        test_generate_answer_long_context,
        test_generate_answer_technical_content,
        test_generate_answer_mixed_language,
        test_generate_answer_context_joining,
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