"""
Unit tests for image-based table detection in PDFs
"""

import sys
from pathlib import Path

# Add parent directory to sys.path to import app modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.ingestion.text_extractor import detect_image_tables_in_pdf


# --- Helper to load PDF bytes ---
def load_pdf_bytes(pdf_path: str) -> bytes:
    """Read PDF file into bytes."""
    with open(pdf_path, "rb") as f:
        return f.read()


# --- Test: PDF with an image-based table ---
def test_image_table_pdf():
    """Test PDF that contains only image-based tables"""
    print("=== Test: PDF with Image Table ===\n")
    
    pdf_bytes = load_pdf_bytes("_local_uploads/sample_table_image.pdf")
    table_info = detect_image_tables_in_pdf(pdf_bytes)
    
    for page in table_info:
        if page['image_table']:
            print(f"✅ Page {page['page']}: Image table detected")
        else:
            print(f"❌ Page {page['page']}: No image table detected")
            assert False, f"Expected an image table on page {page['page']} but none detected"
    
    print()


# --- Run all image table tests ---
def run_all_image_table_tests():
    """Run all tests related to image-based table detection"""
    print("\n" + "="*60)
    print("     IMAGE TABLE DETECTION TESTING")
    print("="*60 + "\n")
    
    tests = [
        test_image_table_pdf,
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
    run_all_image_table_tests()
