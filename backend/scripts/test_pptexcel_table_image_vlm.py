"""
Test PPTX/XLSX table image extraction with VLM.

This script tests the newly integrated VLM table extraction for PowerPoint images.
It should:
1. Extract images from PPTX
2. Detect which images might contain tables
3. Run VLM to extract table content from images
4. Display the extracted table data
"""

import sys
from pathlib import Path

# Add parent directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.ingestion.docling_pptexcel_extractor import parse_pptexcel_with_docling


def test_pptx_with_table_image(file_path: str):
    """Test PPTX file with an image containing a table."""
    
    print("\n" + "="*80)
    print("TEST: PPTX Table Image VLM Extraction")
    print("="*80 + "\n")
    
    # Load test file
    test_file = Path(file_path)
    if not test_file.exists():
        print(f"❌ Test file not found: {file_path}")
        return
    
    print(f"Testing file: {test_file.name}")
    print(f"File size: {test_file.stat().st_size:,} bytes\n")
    
    # Read file bytes
    file_bytes = test_file.read_bytes()
    file_name = test_file.name
    content_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    
    # Parse with Docling + VLM
    print("🔄 Step 1: Parsing PPTX with Docling and VLM...\n")
    
    try:
        parse_result = parse_pptexcel_with_docling(
            file_bytes=file_bytes,
            file_name=file_name,
            content_type=content_type,
            file_id="test-table-image-vlm",
        )
    except Exception as e:
        print(f"❌ Parsing failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Display extraction results
    print("\n" + "-"*80)
    print(" EXTRACTION RESULTS")
    print("-"*80 + "\n")
    
    print(f"✓ Structured blocks: {len(parse_result.structured_blocks)}")
    print(f"✓ Images extracted: {len(parse_result.images)}")
    print(f"✓ Warnings: {len(parse_result.warnings)}")
    
    if parse_result.warnings:
        print("\n⚠️  Warnings:")
        for warning in parse_result.warnings:
            print(f"  - {warning}")
    
    # Display block types
    block_types = {}
    table_image_blocks = []
    
    for block in parse_result.structured_blocks:
        block_types[block.block_type] = block_types.get(block.block_type, 0) + 1
        if block.is_table_image:
            table_image_blocks.append(block)
    
    print(f"\n Block types:")
    for block_type, count in sorted(block_types.items()):
        print(f"  - {block_type}: {count}")
    
    # Display table image blocks with VLM content
    if table_image_blocks:
        print("\n" + "-"*80)
        print("  TABLE IMAGE BLOCKS (with VLM extraction)")
        print("-"*80 + "\n")
        
        for i, block in enumerate(table_image_blocks, 1):
            print(f"Block #{block.block_index} (Page {block.page_no}):")
            print(f"  UUID: {block.table_image_uuid}")
            print(f"  Content preview (first 500 chars):")
            print("  " + "-"*60)
            content_preview = block.content[:500]
            for line in content_preview.split("\n"):
                print(f"  {line}")
            if len(block.content) > 500:
                print(f"  ... ({len(block.content) - 500} more characters)")
            print()
    else:
        print("\n⚠️  No table image blocks found (VLM may be disabled or no tables detected)")
    
    # Display image artifacts
    if parse_result.images:
        print("\n" + "-"*80)
        print("  IMAGE ARTIFACTS")
        print("-"*80 + "\n")
        
        for img in parse_result.images:
            print(f"Image UUID: {img.image_uuid}")
            print(f"  Kind: {img.kind}")
            print(f"  Page: {img.page_no}")
            print(f"  File: {img.file_name}")
            print(f"  S3 status: {img.s3_upload_status}")
            print()
    
    # Display artifact directory
    if parse_result.artifact_dir:
        print(f"\n Artifact directory: {parse_result.artifact_dir}")
        
        # Check for VLM output directories
        vlm_dir = parse_result.artifact_dir / "table_image_vlm"
        if vlm_dir.exists():
            vlm_subdirs = list(vlm_dir.iterdir())
            print(f" VLM output directories: {len(vlm_subdirs)}")
            
            for subdir in vlm_subdirs:
                if subdir.is_dir():
                    print(f"  - {subdir.name}")
                    
                    # Check for output files
                    output_json = subdir / "output.json"
                    summary_txt = subdir / "summary.txt"
                    
                    if output_json.exists():
                        print(f"     output.json ({output_json.stat().st_size} bytes)")
                    if summary_txt.exists():
                        print(f"     summary.txt ({summary_txt.stat().st_size} bytes)")
                        
                        # Display summary content
                        try:
                            summary_content = summary_txt.read_text(encoding="utf-8")
                            print(f"\n     VLM Summary:")
                            print(f"    " + "-"*60)
                            for line in summary_content.split("\n")[:10]:  # First 10 lines
                                print(f"    {line}")
                            if len(summary_content.split("\n")) > 10:
                                print(f"    ... ({len(summary_content.split('\n')) - 10} more lines)")
                        except Exception as e:
                            print(f"    ❌ Could not read summary: {e}")
        else:
            print("⚠️  No VLM output directory (VLM may be disabled)")
    
    print("\n" + "="*80)
    print("✓ TEST COMPLETE")
    print("="*80 + "\n")


if __name__ == "__main__":
    # You can specify a test file path as command line argument
    # Otherwise, use a default test file
    
    if len(sys.argv) > 1:
        test_file_path = sys.argv[1]
    else:
        # Default test file - use the existing sample or specify new one
        test_file_path = "_local_uploads/student_living_accommodation_sample_WITH_TABLE_IMAGE.pptx"
        
        # If the default doesn't exist, try the non-table version
        if not Path(test_file_path).exists():
            test_file_path = "_local_uploads/student_living_accommodation_sample.pptx"
    
    test_pptx_with_table_image(test_file_path)
