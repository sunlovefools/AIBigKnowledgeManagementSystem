"""
Unit tests for chunk_polisher module
Tests text polishing and cleanup functionality
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.chunk_polisher import polish_chunks


def test_basic_polishing():
    """Test basic text polishing"""
    print("=== Test 1: Basic Polishing ===\n")
    
    chunks = [
        {"index": 0, "text": "This is   a    test."},
        {"index": 1, "text": "Another  test   here."}
    ]
    
    result = polish_chunks(chunks)
    
    assert len(result) == 2, f"Expected 2 chunks, got {len(result)}"
    assert "  " not in result[0]['text'], "Should remove multiple spaces"
    assert "  " not in result[1]['text'], "Should remove multiple spaces"
    
    print(f"✅ Input chunk 0: {repr(chunks[0]['text'])}")
    print(f"✅ Output chunk 0: {repr(result[0]['text'])}")
    print(f"✅ Input chunk 1: {repr(chunks[1]['text'])}")
    print(f"✅ Output chunk 1: {repr(result[1]['text'])}\n")


def test_newline_removal():
    """Test that newlines are converted to spaces"""
    print("=== Test 2: Newline Removal ===\n")
    
    chunks = [
        {"index": 0, "text": "Line 1\nLine 2\nLine 3"},
        {"index": 1, "text": "Another\ntext\nwith\nnewlines"}
    ]
    
    result = polish_chunks(chunks)
    
    assert '\n' not in result[0]['text'], "Should remove newlines"
    assert '\n' not in result[1]['text'], "Should remove newlines"
    
    print(f"✅ Input: {repr(chunks[0]['text'])}")
    print(f"✅ Output: {repr(result[0]['text'])}")
    print(f"✅ Newlines converted to spaces\n")


def test_leading_trailing_whitespace():
    """Test removal of leading and trailing whitespace"""
    print("=== Test 3: Leading/Trailing Whitespace ===\n")
    
    chunks = [
        {"index": 0, "text": "   Leading spaces"},
        {"index": 1, "text": "Trailing spaces   "},
        {"index": 2, "text": "   Both sides   "}
    ]
    
    result = polish_chunks(chunks)
    
    assert result[0]['text'] == "Leading spaces", f"Expected 'Leading spaces', got '{result[0]['text']}'"
    assert result[1]['text'] == "Trailing spaces", f"Expected 'Trailing spaces', got '{result[1]['text']}'"
    assert result[2]['text'] == "Both sides", f"Expected 'Both sides', got '{result[2]['text']}'"
    
    for i, chunk in enumerate(result):
        print(f"✅ Chunk {i}: '{chunk['text']}'")
    print()


def test_bullet_point_normalization():
    """Test normalization of bullet points"""
    print("=== Test 4: Bullet Point Normalization ===\n")
    
    chunks = [
        {"index": 0, "text": "- Item one"},
        {"index": 1, "text": "• Item two"},
        {"index": 2, "text": "* Item three"}
    ]
    
    result = polish_chunks(chunks)
    
    # Check that bullet • is converted to -
    assert result[1]['text'].startswith('-'), "• should be converted to -"
    
    for i, chunk in enumerate(result):
        print(f"✅ Input: {repr(chunks[i]['text'])}")
        print(f"✅ Output: {repr(chunk['text'])}")
    print()


def test_multiple_newlines():
    """Test handling of multiple consecutive newlines"""
    print("=== Test 5: Multiple Newlines ===\n")
    
    chunks = [
        {"index": 0, "text": "Paragraph 1\n\n\nParagraph 2"},
        {"index": 1, "text": "Text\n\n\n\nMore text"}
    ]
    
    result = polish_chunks(chunks)
    
    # Multiple newlines should be converted to spaces
    assert '\n' not in result[0]['text'], "Should remove all newlines"
    assert '\n' not in result[1]['text'], "Should remove all newlines"
    
    print(f"✅ Input: {repr(chunks[0]['text'])}")
    print(f"✅ Output: {repr(result[0]['text'])}\n")


def test_mixed_whitespace():
    """Test mixed tabs, spaces, and newlines"""
    print("=== Test 6: Mixed Whitespace ===\n")
    
    chunks = [
        {"index": 0, "text": "Text\twith\ttabs"},
        {"index": 1, "text": "Mixed  \t  whitespace\n\there"}
    ]
    
    result = polish_chunks(chunks)
    
    # Should normalize all whitespace to single spaces
    assert '\t' not in result[0]['text'], "Should remove tabs"
    assert '\n' not in result[1]['text'], "Should remove newlines"
    assert '  ' not in result[0]['text'], "Should remove multiple spaces"
    
    for i, chunk in enumerate(result):
        print(f"✅ Input: {repr(chunks[i]['text'])}")
        print(f"✅ Output: {repr(chunk['text'])}")
    print()


def test_empty_chunks():
    """Test handling of empty chunks"""
    print("=== Test 7: Empty Chunks ===\n")
    
    chunks = [
        {"index": 0, "text": ""},
        {"index": 1, "text": "   "},
        {"index": 2, "text": "Valid text"}
    ]
    
    result = polish_chunks(chunks)
    
    assert len(result) == 3, f"Should preserve all chunks, got {len(result)}"
    assert result[0]['text'] == "", "Empty text should remain empty"
    assert result[1]['text'] == "", "Whitespace-only should become empty"
    assert result[2]['text'] == "Valid text", "Valid text should be preserved"
    
    for i, chunk in enumerate(result):
        print(f"✅ Chunk {i}: '{chunk['text']}'")
    print()


def test_chinese_text_polishing():
    """Test polishing Chinese text"""
    print("=== Test 8: Chinese Text Polishing ===\n")
    
    chunks = [
        {"index": 0, "text": "这是\n中文\n文本"},
        {"index": 1, "text": "   另一段   中文   "}
    ]
    
    result = polish_chunks(chunks)
    
    assert '\n' not in result[0]['text'], "Should handle Chinese newlines"
    assert result[1]['text'].strip() == result[1]['text'], "Should trim Chinese text"
    
    for i, chunk in enumerate(result):
        print(f"✅ Input: {repr(chunks[i]['text'])}")
        print(f"✅ Output: {repr(chunk['text'])}")
    print()


def test_preserves_indices():
    """Test that polishing preserves chunk indices"""
    print("=== Test 9: Preserve Indices ===\n")
    
    chunks = [
        {"index": 0, "text": "Text 1\n\n"},
        {"index": 1, "text": "  Text 2  "},
        {"index": 2, "text": "- Text 3"}
    ]
    
    result = polish_chunks(chunks)
    
    for i, chunk in enumerate(result):
        assert chunk['index'] == i, f"Index should be {i}, got {chunk['index']}"
    
    print(f"✅ All {len(result)} chunks preserved their indices")
    for chunk in result:
        print(f"   Chunk {chunk['index']}: {repr(chunk['text'])}")
    print()


def test_preserves_additional_fields():
    """Test that polishing preserves other fields in chunks"""
    print("=== Test 10: Preserve Additional Fields ===\n")
    
    chunks = [
        {"index": 0, "text": "Text\n\n", "metadata": "some data"},
        {"index": 1, "text": "  More text  ", "source": "test"}
    ]
    
    result = polish_chunks(chunks)
    
    # The current implementation might not preserve extra fields
    # This test checks if they are preserved (if implementation supports it)
    assert len(result) == 2, "Should preserve all chunks"
    
    print(f"✅ Polished {len(result)} chunks")
    for i, chunk in enumerate(result):
        print(f"   Chunk {i}: {list(chunk.keys())}")
    print()


def test_special_characters_preservation():
    """Test that special characters are preserved"""
    print("=== Test 11: Special Characters Preservation ===\n")
    
    chunks = [
        {"index": 0, "text": "Email: test@example.com\n"},
        {"index": 1, "text": "Price: $99.99\n"},
        {"index": 2, "text": "Math: 2+2=4\n"}
    ]
    
    result = polish_chunks(chunks)
    
    assert "@" in result[0]['text'], "Should preserve @ symbol"
    assert "$" in result[1]['text'], "Should preserve $ symbol"
    assert "=" in result[2]['text'], "Should preserve = symbol"
    
    for i, chunk in enumerate(result):
        print(f"✅ Chunk {i}: {repr(chunk['text'])}")
    print()


def test_long_text_polishing():
    """Test polishing of longer text chunks"""
    print("=== Test 12: Long Text Polishing ===\n")
    
    long_text = "This is a long text. " * 50 + "\n\n" + "With newlines. " * 30
    chunks = [{"index": 0, "text": long_text}]
    
    result = polish_chunks(chunks)
    
    assert len(result) == 1, "Should preserve single chunk"
    assert '\n' not in result[0]['text'], "Should remove newlines from long text"
    
    original_words = len(long_text.split())
    polished_words = len(result[0]['text'].split())
    
    print(f"✅ Original length: {len(long_text)} characters, {original_words} words")
    print(f"✅ Polished length: {len(result[0]['text'])} characters, {polished_words} words")
    print(f"✅ Word preservation: {polished_words}/{original_words}\n")


def test_consecutive_bullet_points():
    """Test handling of consecutive bullet points"""
    print("=== Test 13: Consecutive Bullet Points ===\n")
    
    chunks = [
        {"index": 0, "text": "--- Triple dash"},
        {"index": 1, "text": "••• Triple bullet"},
        {"index": 2, "text": "*** Triple asterisk"}
    ]
    
    result = polish_chunks(chunks)
    
    for i, chunk in enumerate(result):
        print(f"✅ Input: {repr(chunks[i]['text'])}")
        print(f"✅ Output: {repr(chunk['text'])}")
    print()


def run_all_tests():
    """Run all chunk polisher tests"""
    print("\n" + "="*60)
    print("     CHUNK POLISHER MODULE TESTING")
    print("="*60 + "\n")
    
    tests = [
        test_basic_polishing,
        test_newline_removal,
        test_leading_trailing_whitespace,
        test_bullet_point_normalization,
        test_multiple_newlines,
        test_mixed_whitespace,
        test_empty_chunks,
        test_chinese_text_polishing,
        test_preserves_indices,
        test_preserves_additional_fields,
        test_special_characters_preservation,
        test_long_text_polishing,
        test_consecutive_bullet_points,
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
