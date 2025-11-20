"""
Master test runner for all ingest module unit tests
Runs tests for all service modules: text_extractor, chunker, and chunk_polisher
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import test modules
from tests import test_text_extractor
from tests import test_chunker
from tests import test_chunk_polisher


def print_header(title):
    """Print a formatted header"""
    print("\n" + "="*70)
    print(f"  {title}")
    print("="*70 + "\n")


def run_all_ingest_tests():
    """Run all ingest-related unit tests"""
    print_header("INGEST MODULE COMPREHENSIVE TESTING SUITE")
    
    all_results = []
    
    # Test 1: Text Extractor
    print("\n" + "🔍 " + "="*65)
    print("   MODULE 1: TEXT EXTRACTOR")
    print("="*70)
    try:
        test_text_extractor.run_all_tests()
        all_results.append(("Text Extractor", True))
    except Exception as e:
        print(f"\n❌ Text Extractor tests failed with error: {e}\n")
        all_results.append(("Text Extractor", False))
    
    # Test 2: Chunker
    print("\n" + "📄 " + "="*65)
    print("   MODULE 2: CHUNKER")
    print("="*70)
    try:
        test_chunker.run_all_tests()
        all_results.append(("Chunker", True))
    except Exception as e:
        print(f"\n❌ Chunker tests failed with error: {e}\n")
        all_results.append(("Chunker", False))
    
    # Test 3: Chunk Polisher
    print("\n" + "✨ " + "="*65)
    print("   MODULE 3: CHUNK POLISHER")
    print("="*70)
    try:
        test_chunk_polisher.run_all_tests()
        all_results.append(("Chunk Polisher", True))
    except Exception as e:
        print(f"\n❌ Chunk Polisher tests failed with error: {e}\n")
        all_results.append(("Chunk Polisher", False))
    
    # Final Summary
    print_header("OVERALL TEST SUMMARY")
    
    print("Module Test Results:")
    print("-" * 70)
    for module_name, passed in all_results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"  {module_name:.<50} {status}")
    
    print("-" * 70)
    
    total = len(all_results)
    passed = sum(1 for _, p in all_results if p)
    failed = total - passed
    
    print(f"\n  Total Modules Tested: {total}")
    print(f"  ✅ Passed: {passed}")
    print(f"  ❌ Failed: {failed}")
    
    if failed == 0:
        print("\n  🎉 ALL INGEST MODULE TESTS PASSED! 🎉")
    else:
        print(f"\n  ⚠️  {failed} module(s) had test failures")
    
    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    run_all_ingest_tests()
