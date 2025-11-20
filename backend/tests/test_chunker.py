"""
Unit tests for chunker module
Tests text chunking functionality with various scenarios
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.chunker import split_into_chunks


def test_basic_chunking():
    """Test basic text chunking with simple paragraphs"""
    print("=== Test 1: Basic Chunking ===\n")
    
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = split_into_chunks(text, max_chars=50)
    
    assert len(chunks) > 0, "Should produce at least one chunk"
    assert all('index' in c and 'text' in c for c in chunks), "Each chunk should have 'index' and 'text'"
    
    print(f"✅ Input text: {len(text)} characters")
    print(f"✅ Generated {len(chunks)} chunks")
    for chunk in chunks:
        print(f"   Chunk {chunk['index']}: {len(chunk['text'])} chars")
    print(f"Chunk[0]{repr(chunks[0]['text'])}\n")


def test_small_text_single_chunk():
    """Test that small text produces only one chunk"""
    print("=== Test 2: Small Text Single Chunk ===\n")
    
    text = "This is a short text."
    chunks = split_into_chunks(text, max_chars=1000)
    
    assert len(chunks) == 1, f"Expected 1 chunk, got {len(chunks)}"
    assert chunks[0]['text'].strip() == text.strip(), "Text content mismatch"
    assert chunks[0]['index'] == 0, "First chunk should have index 0"
    
    print(f"✅ Text: '{text}'")
    print(f"✅ Chunks: {len(chunks)}")
    print(f"✅ Chunk 0: {repr(chunks[0]['text'])}\n")


def test_exact_max_chars():
    """Test text that is exactly max_chars length"""
    print("=== Test 3: Exact Max Chars ===\n")
    
    text = "A" * 100
    chunks = split_into_chunks(text, max_chars=100)
    
    assert len(chunks) == 1, f"Expected 1 chunk, got {len(chunks)}"
    assert len(chunks[0]['text']) == 100, f"Expected 100 chars, got {len(chunks[0]['text'])}"
    
    print(f"✅ Input: {len(text)} characters")
    print(f"✅ Max chars: 100")
    print(f"✅ Result: {len(chunks)} chunk with {len(chunks[0]['text'])} characters\n")


def test_multiple_chunks():
    """Test text that requires multiple chunks"""
    print("=== Test 4: Multiple Chunks ===\n")
    
    paragraphs = [f"Paragraph {i}. " + ("Content " * 20) for i in range(5)]
    text = "\n\n".join(paragraphs)
    
    chunks = split_into_chunks(text, max_chars=200)
    
    assert len(chunks) > 1, "Should produce multiple chunks"
    
    print(f"✅ Input: {len(text)} characters, {len(paragraphs)} paragraphs")
    print(f"✅ Max chars per chunk: 200")
    print(f"✅ Generated {len(chunks)} chunks:")
    for chunk in chunks:
        print(f"   Chunk {chunk['index']}: {len(chunk['text'])} chars")
    print()


def test_chunk_indices():
    """Test that chunk indices are sequential"""
    print("=== Test 5: Sequential Indices ===\n")
    
    text = "\n\n".join([f"Paragraph {i}" for i in range(10)])
    chunks = split_into_chunks(text, max_chars=30)
    
    indices = [c['index'] for c in chunks]
    expected_indices = list(range(len(chunks)))
    
    assert indices == expected_indices, f"Indices should be sequential: expected {expected_indices}, got {indices}"
    
    print(f"✅ Generated {len(chunks)} chunks")
    print(f"✅ Indices: {indices}")
    print(f"✅ All indices are sequential\n")


def test_empty_text():
    """Test chunking empty text"""
    print("=== Test 6: Empty Text ===\n")
    
    text = ""
    chunks = split_into_chunks(text, max_chars=100)
    
    assert len(chunks) == 0, f"Empty text should produce 0 chunks, got {len(chunks)}"
    
    print(f"✅ Empty text handled correctly: {len(chunks)} chunks\n")


def test_whitespace_only():
    """Test text with only whitespace"""
    print("=== Test 7: Whitespace Only ===\n")
    
    text = "   \n\n   \n\n   "
    chunks = split_into_chunks(text, max_chars=100)
    
    assert len(chunks) == 0, f"Whitespace-only text should produce 0 chunks, got {len(chunks)}"
    
    print(f"✅ Whitespace-only text handled correctly: {len(chunks)} chunks\n")


def test_single_long_paragraph():
    """Test a single paragraph longer than max_chars"""
    print("=== Test 8: Single Long Paragraph ===\n")
    
    text = "This is a very long paragraph. " * 50  # ~1500 characters
    chunks = split_into_chunks(text, max_chars=500)
    
    assert len(chunks) > 0, "Should produce at least one chunk"
    
    print(f"✅ Input: {len(text)} characters (no paragraph breaks)")
    print(f"✅ Max chars: 500")
    print(f"✅ Generated {len(chunks)} chunk(s)")
    
    total_chars = sum(len(c['text']) for c in chunks)
    print(f"✅ Total chars in chunks: {total_chars}")
    assert total_chars > 0, "Should preserve some text"
    print()


def test_mixed_paragraph_lengths():
    """Test text with varying paragraph lengths"""
    print("=== Test 9: Mixed Paragraph Lengths ===\n")
    
    paragraphs = [
        "Short.",
        "This is a medium length paragraph with some content.",
        "A" * 100,  # Long paragraph
        "Another short one.",
        "B" * 150,  # Very long paragraph
    ]
    text = "\n\n".join(paragraphs)
    
    chunks = split_into_chunks(text, max_chars=200)
    
    assert len(chunks) > 0, "Should produce at least one chunk"
    
    print(f"✅ Input paragraphs: {len(paragraphs)}")
    print(f"   Lengths: {[len(p) for p in paragraphs]}")
    print(f"✅ Generated {len(chunks)} chunks")
    for chunk in chunks:
        print(f"   Chunk {chunk['index']}: {len(chunk['text'])} chars")
    print()


def test_chinese_text_chunking():
    """Test chunking Chinese text"""
    print("=== Test 10: Chinese Text Chunking ===\n")
    
    paragraphs = [
        "这是第一段中文文本。包含了一些基本的内容。",
        "这是第二段。我们需要测试中文字符的处理。",
        "第三段继续测试。确保分块功能正常工作。",
    ]
    text = "\n\n".join(paragraphs)
    
    chunks = split_into_chunks(text, max_chars=50)
    
    assert len(chunks) > 0, "Should produce at least one chunk"
    
    print(f"✅ Input: {len(text)} characters (Chinese)")
    print(f"✅ Generated {len(chunks)} chunks")
    for chunk in chunks:
        preview = chunk['text'][:30] + "..." if len(chunk['text']) > 30 else chunk['text']
        print(f"   Chunk {chunk['index']}: {len(chunk['text'])} chars - {preview}")
    print()


def test_no_paragraph_breaks():
    """Test text without paragraph breaks (single line)"""
    print("=== Test 11: No Paragraph Breaks ===\n")
    
    text = "This is a single line of text without any paragraph breaks or double newlines. " * 10
    chunks = split_into_chunks(text, max_chars=200)
    
    print(f"✅ Input: {len(text)} characters (no \\n\\n)")
    print(f"✅ Generated {len(chunks)} chunk(s)")
    for chunk in chunks:
        print(f"   Chunk {chunk['index']}: {len(chunk['text'])} chars")
    print()


def test_very_small_max_chars():
    """Test with very small max_chars value"""
    print("=== Test 12: Very Small Max Chars ===\n")
    
    text = "Short paragraph one.\n\nShort paragraph two.\n\nShort paragraph three."
    chunks = split_into_chunks(text, max_chars=10)
    
    print(f"✅ Input: {len(text)} characters")
    print(f"✅ Max chars: 10 (very small)")
    print(f"✅ Generated {len(chunks)} chunks")
    for chunk in chunks:
        print(f"   Chunk {chunk['index']}: {len(chunk['text'])} chars - {repr(chunk['text'][:20])}")
    print()


def test_special_characters_in_text():
    """Test text with special characters and symbols"""
    print("=== Test 13: Special Characters ===\n")
    
    text = "Paragraph with @#$%^&*().\n\nAnother with 123456.\n\nAnd symbols: ©®™§"
    chunks = split_into_chunks(text, max_chars=100)
    
    assert len(chunks) > 0, "Should handle special characters"
    
    print(f"✅ Input with special characters: {len(text)} chars")
    print(f"✅ Generated {len(chunks)} chunks")
    for chunk in chunks:
        print(f"   Chunk {chunk['index']}: {repr(chunk['text'][:40])}")
    print()


def run_all_tests():
    """Run all chunker tests"""
    print("\n" + "="*60)
    print("        CHUNKER MODULE TESTING")
    print("="*60 + "\n")
    
    tests = [
        test_basic_chunking,
        test_small_text_single_chunk,
        test_exact_max_chars,
        test_multiple_chunks,
        test_chunk_indices,
        test_empty_text,
        test_whitespace_only,
        test_single_long_paragraph,
        test_mixed_paragraph_lengths,
        test_chinese_text_chunking,
        test_no_paragraph_breaks,
        test_very_small_max_chars,
        test_special_characters_in_text,
    ]
    
    passed = 0
    failed = 0
    
    for test_func in tests:
        try:
            test_func()
            passed += 1
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
    run_all_tests()
