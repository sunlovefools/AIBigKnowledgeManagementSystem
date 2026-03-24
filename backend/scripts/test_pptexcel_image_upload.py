"""
Test uploading PowerPoint/Excel (PPTX/XLSX) documents with images to verify image extraction.
"""

import requests
from pathlib import Path

# Test files in _local_uploads
TEST_FILES = [
    Path("_local_uploads/student_living_accommodation_sample_WITH_TABLE.pptx"),
    Path("_local_uploads/student_living_accommodation_sample.xlsx"),
]

def upload_pptexcel_document(file_path: Path):
    """Upload a PowerPoint/Excel document and check the response."""
    print(f"\n{'='*60}")
    print(f"Testing: {file_path.name}")
    print(f"{'='*60}")
    
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return
    
    url = "http://127.0.0.1:8000/ingest/upload"
    
    with open(file_path, "rb") as f:
        files = {"file": (file_path.name, f, "application/octet-stream")}
        params = {"strategy": "docling-pptexcel"}
        
        try:
            response = requests.post(url, files=files, params=params, timeout=120)
            
            if response.status_code == 200:
                result = response.json()
                print(f"\n✅ Upload successful!")
                print(f"   File ID: {result.get('file_id')}")
                print(f"   Chunks created: {result.get('chunks_created', 0)}")
                print(f"   Parent chunks: {result.get('parent_chunks_count', 0)}")
                print(f"   Child chunks: {result.get('child_chunks_count', 0)}")
                
                if "warnings" in result and result["warnings"]:
                    print(f"\n⚠️  Warnings:")
                    for warning in result["warnings"]:
                        print(f"   - {warning}")
                
                # Check for images
                if "images_extracted" in result:
                    print(f"\n  Images extracted: {result['images_extracted']}")
                
                return result
            else:
                print(f"\n❌ Upload failed: {response.status_code}")
                print(f"   Response: {response.text[:500]}")
                
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    print("PowerPoint/Excel Image Extraction Test")
    print("="*60)
    
    if not TEST_FILES:
        print("\n⚠️  No test files configured.")
        print("Please add PowerPoint/Excel files with images to TEST_FILES list.")
        print("\nExample:")
        print('TEST_FILES = [')
        print('    Path("c:/Users/c9798/team44_project/backend/_local_uploads/sample.pptx"),')
        print(']')
    else:
        for file_path in TEST_FILES:
            upload_pptexcel_document(file_path)
    
    print(f"\n{'='*60}")
    print("Test complete!")
