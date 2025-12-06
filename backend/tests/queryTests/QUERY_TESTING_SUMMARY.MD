# Query Module Testing Summary

## Overview

This document provides a comprehensive summary of the test coverage for the backend Query Module. Testing is divided into two categories:
- Integration Tests: End-to-end testing of the complete RAG query pipeline
- Unit Tests: Testing individual service module functionality

## Query Module Architecture

The Query module consists of 2 core service modules:

```
1. Query Refiner (query_refiner) - Refines and optimizes user queries
2. Answer Generator (answer_generator) - Generates answers from RAG contents
```

Note: The complete query pipeline also calls external services (vector_store for retrieval), but these are shared services used by multiple modules and are not part of the query core functionality.

## Test File Structure

```
tests/queryTests/
├── test_query.py                    # Integration tests (8 tests)
├── test_query_refiner.py           # Query refiner unit tests (7 tests)
├── test_answer_generator.py        # Answer generator unit tests (8 tests)
└── test_all_query_modules.py      # Master test runner
```

Total: 23 test cases

## Integration Test Details (test_query.py)

### Test 1: test_query_pipeline_basic()
**Purpose**: Test complete query pipeline with basic English query

- Input Query: "What is machine learning?"
- Top K: 3
- Validation Points:
  - Query refinement successful
  - Vector retrieval successful
  - Answer generation successful
  - End-to-end pipeline completes without errors
- Expected Result: Complete RAG pipeline produces coherent answer

### Test 2: test_query_pipeline_chinese()
**Purpose**: Test query pipeline with Chinese query

- Input Query: "什么是深度学习？"
- Top K: 3
- Validation Points:
  - Chinese query correctly processed
  - Retrieval works with Chinese text
  - Answer generated in appropriate language
  - End-to-end pipeline completes without errors
- Expected Result: Chinese query successfully processed through RAG pipeline

### Test 3: test_query_pipeline_different_top_k()
**Purpose**: Test query pipeline with different top_k values

- Input Query: "Explain neural networks"
- Top K Values: [1, 3, 5, 10]
- Validation Points:
  - Each top_k value handled correctly
  - Results scale appropriately with top_k
  - No errors for any top_k value
- Expected Result: Pipeline adapts correctly to different retrieval sizes

### Test 4: test_query_direct_endpoint()
**Purpose**: Test direct query endpoint (without refinement)

- Input Query: "What is Python programming?"
- Top K: 3
- Features: Bypasses query refinement step
- Validation Points:
  - Direct endpoint functions correctly
  - Answer generation works without refinement
  - Results comparable to refined queries
- Expected Result: Direct query endpoint produces valid answers

### Test 5: test_query_long_query()
**Purpose**: Test query pipeline with long, complex query

- Input: 171-character multi-part question about AI concepts
- Query Content: Requests comprehensive explanation of AI, ML, deep learning, neural networks, and real-world applications
- Top K: 5
- Validation Points:
  - Long query correctly processed
  - Multiple concepts handled in single query
  - Comprehensive answer generated
- Expected Result: Long queries produce complete, coherent answers

### Test 6: test_query_special_characters()
**Purpose**: Test query pipeline with special characters

- Input Query: "What's the difference between Python 3.9 & 3.10? @#$"
- Special Characters: Apostrophes, ampersands, symbols
- Validation Points:
  - Special characters don't break pipeline
  - Query intent preserved
  - Answer addresses actual question
- Expected Result: Special characters handled gracefully

### Test 7: test_query_technical_terms()
**Purpose**: Test query pipeline with technical terminology

- Input Query: "Explain backpropagation in CNN with ReLU activation function"
- Technical Terms: backpropagation, CNN, ReLU, activation function
- Top K: 5
- Validation Points:
  - Technical terms preserved in refinement
  - Relevant technical content retrieved
  - Technical answer generated accurately
- Expected Result: Technical queries produce precise, accurate answers

### Test 8: test_query_mixed_language()
**Purpose**: Test query pipeline with mixed language query

- Input Query: "How does 机器学习 machine learning 工作?"
- Languages: English and Chinese mixed
- Validation Points:
  - Mixed language query processed correctly
  - Retrieval handles multilingual input
  - Answer addresses query appropriately
- Expected Result: Mixed language queries handled successfully

## Unit Test Details

### 1. Query Refiner Tests (test_query_refiner.py)

7 test cases validating query refinement functionality.

#### Test 1: test_refine_query_basic()
- Purpose: Test basic query refinement
- Input: "What is machine learning?"
- Validation:
  - Returns non-null string result
  - Result is not empty
  - Query is properly refined

#### Test 2: test_refine_query_chinese()
- Purpose: Test Chinese query refinement
- Input: "什么是深度学习？"
- Validation:
  - Chinese characters handled correctly
  - Returns valid refined query
  - Character encoding preserved

#### Test 3: test_refine_query_long()
- Purpose: Test refinement of long, complex query
- Input: 161-character query about neural networks (CNN vs RNN)
- Original Query: "I want to understand how neural networks work and what are the differences between CNN and RNN and when should I use each one in my projects"
- Validation:
  - Long queries processed completely
  - Multiple concepts preserved
  - Result maintains query intent

#### Test 4: test_refine_query_special_characters()
- Purpose: Test special character handling
- Input: "What is the difference between Python 3.9 & 3.10? @#$%"
- Characters Tested: &, ?, @, #, $, %
- Validation:
  - Special characters don't cause errors
  - Query meaning preserved

#### Test 5: test_refine_query_short()
- Purpose: Test very short query refinement
- Input: "AI?"
- Length: 3 characters
- Validation:
  - Short queries handled properly
  - Returns valid result even for minimal input

#### Test 6: test_refine_query_mixed_language()
- Purpose: Test mixed language query
- Input: "How does machine learning 机器学习 work?"
- Validation:
  - Both English and Chinese handled
  - Mixed content preserved correctly

#### Test 7: test_refine_query_technical()
- Purpose: Test technical terminology refinement
- Input: "Explain backpropagation in CNN with ReLU activation"
- Technical Terms: backpropagation, CNN, ReLU activation
- Validation:
  - Technical terms preserved accurately
  - Domain-specific language maintained

### 2. Answer Generator Tests (test_answer_generator.py)

8 test cases validating answer generation functionality.

#### Test 1: test_generate_answer_basic()
- Purpose: Test basic answer generation
- RAG Contents:
  - "Machine learning is a subset of artificial intelligence that enables systems to learn from data."
  - "Deep learning uses neural networks with multiple layers to process complex patterns."
- User Query: "What is machine learning?"
- Validation:
  - Non-null string result
  - Answer not empty
  - Answer length reasonable

#### Test 2: test_generate_answer_single_chunk()
- Purpose: Test answer generation with single context chunk
- RAG Content: "Python is a high-level programming language known for its simplicity and readability."
- User Query: "What is Python?"
- Validation:
  - Single chunk handled correctly
  - Complete answer generated from limited context

#### Test 3: test_generate_answer_multiple_chunks()
- Purpose: Test answer generation with multiple context chunks
- RAG Contents: 5 chunks about neural networks
  - Neural networks overview
  - CNNs for image recognition
  - RNNs for sequential data
  - Transformers with attention mechanisms
  - Deep learning training requirements
- User Query: "Explain different types of neural networks"
- Validation:
  - Multiple chunks synthesized correctly
  - Comprehensive answer generated
  - All relevant information included

#### Test 4: test_generate_answer_chinese()
- Purpose: Test answer generation with Chinese content
- RAG Contents: 3 Chinese chunks about AI
  - 人工智能定义
  - 机器学习概述
  - 深度学习与神经网络
- User Query: "什么是人工智能？"
- Validation:
  - Chinese content processed correctly
  - Answer generated appropriately
  - Character encoding handled properly

#### Test 5: test_generate_answer_long_context()
- Purpose: Test answer generation with long context
- RAG Contents: 2 chunks, each ~1,600 characters
- Total Context: ~3,200 characters
- User Query: "Summarize the impact of AI"
- Validation:
  - Large context handled efficiently
  - Answer remains coherent
  - No truncation issues

#### Test 6: test_generate_answer_technical_content()
- Purpose: Test answer generation with technical content
- RAG Contents: 4 technical chunks
  - Backpropagation algorithm
  - Learning rate hyperparameter
  - Batch normalization
  - Dropout regularization
- User Query: "How does neural network training work?"
- Validation:
  - Technical accuracy maintained
  - Specialized terminology used correctly
  - Comprehensive technical answer

#### Test 7: test_generate_answer_mixed_language()
- Purpose: Test answer generation with mixed language content
- RAG Contents: 3 mixed English-Chinese chunks
  - "Machine learning (机器学习) is a powerful technology."
  - "Deep learning 深度学习 enables complex pattern recognition."
  - "AI applications include 图像识别 and natural language processing."
- User Query: "What are AI applications?"
- Validation:
  - Mixed language content handled
  - Answer coherent despite mixed input

#### Test 8: test_generate_answer_context_joining()
- Purpose: Test that multiple chunks are properly joined
- RAG Contents: 3 simple chunks
  - "First chunk of information."
  - "Second chunk of information."
  - "Third chunk of information."
- User Query: "What information is provided?"
- Validation:
  - Chunks correctly combined
  - Answer reflects multiple sources
  - Context joining mechanism verified

## Test Coverage Statistics

| Module | Test File | Test Count | Status |
|--------|-----------|------------|--------|
| Integration Tests | test_query.py | 8 | All Passed |
| Query Refiner | test_query_refiner.py | 7 | All Passed |
| Answer Generator | test_answer_generator.py | 8 | All Passed |
| Total | - | 23 | 100% Pass Rate |

## Test Coverage Scope

### Functional Coverage
- Query refinement (optimization for retrieval)
- Answer generation (from RAG contents)
- End-to-end RAG pipeline
- Direct query endpoint (no refinement)
- Multiple top_k configurations

### Edge Case Coverage
- Very short queries (3 characters)
- Long queries (160+ characters)
- Empty or minimal context
- Large context (3000+ characters)
- Technical terminology
- Special characters (@#$%^&*())

### Internationalization Coverage
- Chinese queries and content
- English queries and content
- Mixed language queries
- Mixed language content
- Unicode character support

### Data Integrity Coverage
- Context chunk joining
- Multi-chunk synthesis
- Query intent preservation
- Technical accuracy maintenance
- Character encoding correctness (UTF-8)

## Running Tests

### Run All Tests
```bash
cd backend
python tests/queryTests/test_all_query_modules.py
```

### Run Individual Test Modules
```bash
# Integration tests
python tests/queryTests/test_query.py

# Query refiner tests
python tests/queryTests/test_query_refiner.py

# Answer generator tests
python tests/queryTests/test_answer_generator.py
```

## Key Configuration

### Query Configuration
- Default Top K: 3-5 retrieved chunks
- Supports: 1-10 top_k values
- Query Refinement: Optional (can be bypassed with direct endpoint)

### LLM Integration
- Query Refiner: Uses Beam LLM API (BEAM_REFINE_LLM_URL)
- Answer Generator: Uses Beam LLM API (separate endpoint)
- Environment Variables: BEAM_REFINE_LLM_URL, BEAM_REFINE_LLM_KEY

### Supported Features
1. Query Refinement - Optimizes queries for better retrieval
2. Direct Query - Bypasses refinement for speed
3. Multi-language Support - English, Chinese, and mixed
4. Variable Retrieval Size - Configurable top_k parameter

## Test Design Philosophy

### Separation of Concerns
1. query_refiner: Optimizes query for retrieval without changing intent
2. answer_generator: Synthesizes coherent answers from retrieved chunks
3. Integration tests: Validate complete RAG pipeline functionality

Note: vector_store is a separate shared service, not part of query core modules.

### Testing Strategy
1. Unit Tests: Independently test each module's functionality
2. Integration Tests: Test complete end-to-end RAG pipeline
3. Edge Testing: Cover extreme cases and exceptional inputs
4. Language Testing: Validate multilingual support
5. Regression Testing: Ensure modifications don't affect existing functionality

## Test Results Example

### Successful Output
```
======================================================================
  QUERY MODULE COMPREHENSIVE TESTING SUITE
======================================================================

🔍 ==================================================================
   MODULE 1: QUERY REFINER
======================================================================
Passed:  7
Failed:  0
Total:   7

🧠 ==================================================================
   MODULE 2: ANSWER GENERATOR
======================================================================
Passed:  8
Failed:  0
Total:   8

🔗 ==================================================================
   MODULE 3: QUERY INTEGRATION (END-TO-END)
======================================================================
Passed:  8
Failed:  0
Total:   8

======================================================================
              OVERALL TEST SUMMARY
======================================================================
Module Test Results:
----------------------------------------------------------------------
  Query Refiner.......................................... ✅ PASSED
  Answer Generator....................................... ✅ PASSED
  Query Integration...................................... ✅ PASSED
----------------------------------------------------------------------

  Total Modules Tested: 3
  ✅ Passed: 3
  ❌ Failed: 0

  🎉 ALL QUERY MODULE TESTS PASSED! 🎉

======================================================================
```

## Related Documentation

- backend/app/api/router_query.py - Query API router
- backend/app/service/query_refiner.py - Query refinement service
- backend/app/service/answer_generator.py - Answer generation service
- backend/app/service/vector_store.py - Vector storage and retrieval service
- backend/app/service/embedder.py - Embedding generation service

## Test Maintenance

### When to Update Tests
1. When modifying query refinement logic
2. When changing answer generation prompts
3. When updating LLM API endpoints
4. When modifying retrieval parameters
5. When adding new language support

### Testing Best Practices
1. Run tests after every code modification
2. Write corresponding tests when adding new features
3. Maintain test independence (no dependencies on other tests)
4. Use meaningful test data (realistic queries)
5. Include both positive and negative test cases
6. Test edge cases (empty, very short, very long)
7. Validate multilingual support

## Known Considerations

### LLM API Dependencies
- Tests require valid Beam LLM API credentials
- Environment variables must be set: BEAM_REFINE_LLM_URL, BEAM_REFINE_LLM_KEY
- Network connectivity required for API calls

### Vector Store Dependencies
- Integration tests require functional vector store
- Test database must be seeded with sample documents
- Retrieval results may vary based on database state

### Performance Considerations
- LLM API calls add latency to test execution
- Large context tests may take longer to complete
- Consider using test timeouts for async operations

## Summary

This test suite provides 23 comprehensive test cases covering all key functionalities of the Query module:

- Query Refinement: Optimizes queries for better retrieval while preserving intent
- Answer Generation: Synthesizes coherent answers from multiple context chunks
- End-to-End Pipeline: Complete RAG workflow validation from query to answer
- Multilingual Support: Handles English, Chinese, and mixed language content
- Error Handling: Gracefully handles edge cases and exceptional inputs

Key Features Tested:
- ✅ Query optimization and refinement
- ✅ Context-aware answer generation
- ✅ Multiple chunk synthesis
- ✅ Variable retrieval size (top_k)
- ✅ Direct query endpoint (bypass refinement)
- ✅ Chinese language support
- ✅ Mixed language handling
- ✅ Technical terminology preservation
- ✅ Special character handling
- ✅ Long query processing

Test Pass Rate: 100%
Code Coverage: Comprehensive coverage of Query module core functionality
Maintenance Status: MVP stage, stable and reliable

## Future Test Enhancements

### Potential Additions
1. Performance benchmarking tests
2. Concurrent query handling tests
3. Cache effectiveness tests
4. Error recovery tests
5. Rate limiting tests
6. Response quality evaluation tests
7. More language support tests (Spanish, French, Japanese)

### Integration Improvements
1. Mock LLM responses for faster unit tests
2. Dedicated test vector store with known content
3. Automated test data generation
4. Test coverage reporting integration
5. Continuous integration pipeline setup