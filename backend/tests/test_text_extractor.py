"""
Unit tests for text_extractor module
Tests text extraction from different file formats
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.text_extractor import extract_text, SUPPORTED


def test_supported_formats():
    """Test that all expected formats are supported"""
    print("=== Test 1: Supported Formats ===\n")
    
    expected_formats = {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
    }
    
    assert SUPPORTED == expected_formats, f"Expected {expected_formats}, got {SUPPORTED}"
    print(f"✅ All {len(SUPPORTED)} formats are correctly defined")
    print(f"   Formats: {', '.join(SUPPORTED)}\n")


def test_plain_text_extraction():
    """Test extracting text from plain text format"""
    print("=== Test 2: Plain Text Extraction ===\n")
    
    # Test case 1: Simple English text
    text1 = "Hello, World!"
    data1 = text1.encode('utf-8')
    result1 = extract_text("text/plain", data1)
    assert result1 == text1, f"Expected '{text1}', got '{result1}'"
    print(f"✅ English text: '{result1}'")
    
    # Test case 2: Chinese text
    text2 = "你好，世界！"
    data2 = text2.encode('utf-8')
    result2 = extract_text("text/plain", data2)
    assert result2 == text2, f"Expected '{text2}', got '{result2}'"
    print(f"✅ Chinese text: '{result2}'")
    
    # Test case 3: Mixed text with special characters
    text3 = "Hello 世界! @#$%^&*()"
    data3 = text3.encode('utf-8')
    result3 = extract_text("text/plain", data3)
    assert result3 == text3, f"Expected '{text3}', got '{result3}'"
    print(f"✅ Mixed text: '{result3}'")
    
    # Test case 4: Multi-line text
    text4 = "Line 1\nLine 2\nLine 3"
    data4 = text4.encode('utf-8')
    result4 = extract_text("text/plain", data4)
    assert result4 == text4, f"Expected '{text4}', got '{result4}'"
    print(f"✅ Multi-line text: {repr(result4)}\n")


def test_unsupported_format():
    """Test that unsupported formats raise ValueError"""
    print("=== Test 3: Unsupported Format Handling ===\n")
    
    unsupported_formats = [
        "image/jpeg",
        "image/png",
        "video/mp4",
        "audio/mp3",
        "application/zip",
    ]
    
    for content_type in unsupported_formats:
        try:
            extract_text(content_type, b"dummy data")
            print(f"❌ Should have raised ValueError for {content_type}")
            assert False, f"Expected ValueError for {content_type}"
        except ValueError as e:
            print(f"✅ Correctly rejected: {content_type}")
            # The error message is "Unsupported contentType"
            assert str(e) == "Unsupported contentType", f"Error message mismatch: {str(e)}"
    
    print()


def test_empty_text():
    """Test extracting empty text"""
    print("=== Test 4: Empty Text Handling ===\n")
    
    empty_data = b""
    result = extract_text("text/plain", empty_data)
    assert result == "", f"Expected empty string, got '{result}'"
    print(f"✅ Empty text handled correctly: '{result}'\n")


def test_large_text():
    """Test extracting large text"""
    print("=== Test 5: Large Text Extraction ===\n")
    
    # Create a large text (10KB)
    large_text = "A" * 10000
    data = large_text.encode('utf-8')
    result = extract_text("text/plain", data)
    
    assert len(result) == 10000, f"Expected length 10000, got {len(result)}"
    assert result == large_text, "Text content mismatch"
    print(f"✅ Large text extracted successfully: {len(result)} characters\n")


def test_text_with_unicode():
    """Test extracting text with various Unicode characters"""
    print("=== Test 6: Unicode Text Extraction ===\n")
    
    unicode_texts = [
        "Hello 世界 مرحبا мир",  # Multiple languages
        "Emoji test: 😀 🎉 🚀",  # Emojis
        "Math symbols: ∑ ∫ √ π",  # Mathematical symbols
        "Currency: $ € ¥ £",  # Currency symbols
    ]
    
    for text in unicode_texts:
        data = text.encode('utf-8')
        result = extract_text("text/plain", data)
        assert result == text, f"Unicode text mismatch: expected '{text}', got '{result}'"
        print(f"✅ {text}")
    
    print()


def test_text_with_special_whitespace():
    """Test text with tabs, newlines, and multiple spaces"""
    print("=== Test 7: Special Whitespace Handling ===\n")
    
    test_cases = [
        ("Text\twith\ttabs", "Text with tabs"),
        ("Multiple  spaces  here", "Multiple spaces"),
        ("\n\nNewlines\n\n", "Newlines"),
        ("  Leading and trailing  ", "Leading and trailing"),
    ]
    
    for input_text, description in test_cases:
        data = input_text.encode('utf-8')
        result = extract_text("text/plain", data)
        assert result == input_text, f"Whitespace handling error"
        print(f"✅ {description}: {repr(result)}")
    
    print()


def run_all_tests():
    """Run all text extractor tests"""
    print("\n" + "="*60)
    print("     TEXT EXTRACTOR MODULE TESTING")
    print("="*60 + "\n")
    
    tests = [
        test_supported_formats,
        test_plain_text_extraction,
        test_unsupported_format,
        test_empty_text,
        test_large_text,
        test_text_with_unicode,
        test_text_with_special_whitespace,
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
