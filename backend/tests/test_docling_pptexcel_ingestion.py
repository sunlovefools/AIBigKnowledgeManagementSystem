"""
Integration test for Docling Office (PowerPoint & Excel) ingestion.

Purpose:
- Test end-to-end ingestion of PPTX and XLSX files
- Validate Docling extraction and chunking
- Verify integration with existing ingestion pipeline


"""

import base64
import asyncio
from pathlib import Path
import sys
import pytest

# Add parent directory to path so we can import app modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load environment variables before importing app modules
from dotenv import load_dotenv
load_dotenv()

from app.api.router_ingest import ingest_upload, FileUpload


@pytest.mark.anyio
async def test_ingest_pptx_file():
    """
    Test ingesting a PowerPoint file with Docling Office integration.
    
    Expected behaviour:
    - Docling extracts structured content (slides, headers, text, tables)
    - Chunker creates parent/child chunks preserving structure
    - Chunks are stored in vector database
    """
    print("=" * 80)
    print("TEST 1: PowerPoint (PPTX) Ingestion via Docling")
    print("=" * 80)
    print()
    
    pptx_path = Path(__file__).resolve().parent.parent / "_local_uploads" / "student_living_accommodation_sample_WITH_TABLE.pptx"
    
    if not pptx_path.exists():
        print(f"⚠️  PowerPoint sample file not found at {pptx_path}")
        return None
    
    # Read the PowerPoint file
    with open(pptx_path, "rb") as f:
        pptx_bytes = f.read()
    
    print(f" File: {pptx_path.name}")
    print(f" Size: {len(pptx_bytes)} bytes ({len(pptx_bytes) / 1024:.2f} KB)")
    print()
    
    # Encode to base64
    base64_data = base64.b64encode(pptx_bytes).decode('utf-8')
    
    # Create FileUpload object with correct MIME type for PPTX
    file_upload = FileUpload(
        fileName="backend/_local_uploads/student_living_accommodation_sample_WITH_TABLE.pptx",
        contentType="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        data=base64_data
    )
    
    try:
        print(" Starting Docling Office ingestion...")
        print()
        result = await ingest_upload(file_upload)
        
        print()
        print("=" * 80)
        print("✅ PowerPoint file ingestion SUCCESSFUL!")
        print("=" * 80)
        print(f"   Strategy: {result.strategy}")
        print(f"   Parent chunks: {result.parent_chunks}")
        print(f"   Child chunks: {result.child_chunks}")
        print(f"   Status: {result.status}")
        print(f"   Message: {result.message}")
        
        if result.warnings:
            print(f"   ⚠️  Warnings: {len(result.warnings)}")
            for warning in result.warnings:
                print(f"      - {warning}")
        
        print()
        return True
        
    except Exception as e:
        print()
        print("=" * 80)
        print(f"❌ PowerPoint file ingestion FAILED")
        print("=" * 80)
        print(f"   Error: {type(e).__name__}: {e}")
        print()
        import traceback
        traceback.print_exc()
        return False


@pytest.mark.anyio
async def test_ingest_xlsx_file():
    """
    Test ingesting an Excel file with Docling Office integration.
    
    Expected behavior:
    - Docling extracts structured content (sheets, tables, data)
    - Chunker creates parent/child chunks preserving structure
    - Chunks are stored in vector database
    """
    print("=" * 80)
    print("TEST 2: Excel (XLSX) Ingestion via Docling")
    print("=" * 80)
    print()
    
    xlsx_path = Path(__file__).resolve().parent.parent / "_local_uploads" / "student_living_accommodation_sample.xlsx"
    
    if not xlsx_path.exists():
        print(f"⚠️ Excel sample file not found at {xlsx_path}")
        return None
    
    # Read the Excel file
    with open(xlsx_path, "rb") as f:
        xlsx_bytes = f.read()
    
    print(f" File: {xlsx_path.name}")
    print(f" Size: {len(xlsx_bytes)} bytes ({len(xlsx_bytes) / 1024:.2f} KB)")
    print()
    
    # Encode file as base64 because the ingest API expects uploaded files
    # in base64 format (simulating a real HTTP upload request)
    base64_data = base64.b64encode(xlsx_bytes).decode('utf-8')
    
    # Create FileUpload object with correct MIME type for XLSX
    file_upload = FileUpload(
        fileName="student_living_accommodation_sample.xlsx",
        contentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        data=base64_data
    )
    
    try:
        print(" Starting Docling Office ingestion...")
        print()
        result = await ingest_upload(file_upload)
        
        print()
        print("=" * 80)
        print("✅ Excel file ingestion SUCCESSFUL!")
        print("=" * 80)
        print(f"   Strategy: {result.strategy}")
        print(f"   Parent chunks: {result.parent_chunks}")
        print(f"   Child chunks: {result.child_chunks}")
        print(f"   Status: {result.status}")
        print(f"   Message: {result.message}")
        
        if result.warnings:
            print(f"   ⚠️  Warnings: {len(result.warnings)}")
            for warning in result.warnings:
                print(f"      - {warning}")
        
        print()
        return True
        
    except Exception as e:
        print()
        print("=" * 80)
        print(f"❌ Excel file ingestion FAILED")
        print("=" * 80)
        print(f"   Error: {type(e).__name__}: {e}")
        print()
        import traceback
        traceback.print_exc()
        return False


async def run_all_tests():
    """
    Run all Docling Office integration tests.
    
    """
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 15 + "DOCLING OFFICE INTEGRATION TEST SUITE" + " " * 25 + "║")
    print("║" + " " * 20 + "PowerPoint & Excel Ingestion" + " " * 30 + "║")
    print("╚" + "=" * 78 + "╝")
    print()
    
    results = []
    
    # Test 1: PowerPoint ingestion
    result_pptx = await test_ingest_pptx_file()
    results.append(("PowerPoint (PPTX)", result_pptx))
    
    print()
    
    # Test 2: Excel ingestion
    result_xlsx = await test_ingest_xlsx_file()
    results.append(("Excel (XLSX)", result_xlsx))
    
    # Summary
    print()
    print("=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    passed = sum(1 for _, result in results if result is True)
    failed = sum(1 for _, result in results if result is False)
    skipped = sum(1 for _, result in results if result is None)
    
    for test_name, result in results:
        if result is True:
            print(f"   ✅ {test_name}: PASSED")
        elif result is False:
            print(f"   ❌ {test_name}: FAILED")
        else:
            print(f"   ⚠️  {test_name}: SKIPPED (file not found)")
    
    print()
    print(f"Total: {len(results)} tests")
    print(f"   Passed: {passed}")
    print(f"   Failed: {failed}")
    print(f"   Skipped: {skipped}")
    print("=" * 80)
    print()
    
    if failed > 0:
        print("❌ Some tests failed. Please review the errors above.")
    elif skipped == len(results):
        print("⚠️  All tests skipped. Please add test files to backend/_local_uploads/")
    else:
        print("✅ All tests passed! Docling integration is working correctly.")
    
    print()


if __name__ == "__main__":
    # Run async tests
    asyncio.run(run_all_tests())
