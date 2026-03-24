"""
Integration test for the full PPTX/XLSX ingestion pipeline.

Pipeline tested:
    File → Docling extraction → Structured blocks → Parent/Child chunking → Output preview

Purpose:
    - Validate Docling extraction for PowerPoint/Excel
    - Verify chunking logic
    - Inspect sample output chunks for correctness

Run manually with:
    python tests/test_full_pipeline.py
    
"""

from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.rag.ingestion.docling_pptexcel_extractor import parse_pptexcel_with_docling
from app.service.rag.ingestion.docling_chunker import split_parent_child_chunks_from_docling_blocks

def test_full_pipeline(file_path: Path, content_type: str):
    """Test complete pipeline: parse → chunk → display"""
    print("\n" + "="*80)
    print(f"FULL PIPELINE TEST: {file_path.name}")
    print("="*80)
    
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return
    
    # Read file bytes
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    
    print(f"\n📄 File: {file_path.name} ({len(file_bytes):,} bytes)")
    
    # STEP 1: Parse with Docling (extraction)
    print("\n" + "-"*80)
    print("STEP 1: Docling Extraction")
    print("-"*80)
    
    try:
        parse_result = parse_pptexcel_with_docling(
            file_bytes=file_bytes,
            file_name=file_path.name,
            content_type=content_type,
            file_id="test-full-pipeline-001",
        )
        
        print(f"✅ Extraction successful!")
        print(f"   - Structured blocks: {len(parse_result.structured_blocks)}")
        print(f"   - Images extracted: {len(parse_result.images)}")
        print(f"   - Warnings: {len(parse_result.warnings)}")
        print(f"   - Artifact directory: {parse_result.artifact_dir}")
        
        if parse_result.warnings:
            print(f"\n⚠️  Warnings:")
            for warning in parse_result.warnings:
                print(f"   - {warning}")
        
        # Show block types
        block_types = {}
        for block in parse_result.structured_blocks:
            block_type = block.block_type
            block_types[block_type] = block_types.get(block_type, 0) + 1
        
        print(f"\n Block type distribution:")
        for block_type, count in sorted(block_types.items()):
            print(f"   - {block_type}: {count}")
        
    except Exception as e:
        print(f"❌ Extraction failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # STEP 2: Chunking
    print("\n" + "-"*80)
    print("STEP 2: Parent/Child Chunking")
    print("-"*80)
    
    try:
        parent_chunks, child_chunks = split_parent_child_chunks_from_docling_blocks(
            blocks=parse_result.structured_blocks,
            file_name=file_path.name,
            artifact_dir=parse_result.artifact_dir,
            file_id="test-full-pipeline-001",
        )
        
        print(f"✅ Chunking successful!")
        print(f"   - Parent chunks: {len(parent_chunks)}")
        print(f"   - Child chunks: {len(child_chunks)}")
        
    except Exception as e:
        print(f"❌ Chunking failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # STEP 3: Display Sample Results
    print("\n" + "-"*80)
    print("STEP 3: Sample Results")
    print("-"*80)
    
    # Show first 3 parent chunks
    print(f"\n Parent Chunks (showing first 3 of {len(parent_chunks)}):")
    for i, chunk in enumerate(parent_chunks[:3], 1):
        print(f"\n   Parent Chunk {i}:")
        print(f"   - ID: {chunk.parent_chunk_id}")
        print(f"   - Type: {chunk.parent_chunk_metadata.get('block_type', 'N/A')}")
        print(f"   - Content length: {len(chunk.content)} chars")
        print(f"   - Content preview: {chunk.content[:150]}...")
        if chunk.parent_chunk_metadata.get('image_uuid'):
            print(f"   - 🖼️  Contains image: {chunk.parent_chunk_metadata['image_uuid']}")
    
    # Show first 5 child chunks
    print(f"\n Child Chunks (showing first 5 of {len(child_chunks)}):")
    for i, chunk in enumerate(child_chunks[:5], 1):
        print(f"\n   Child Chunk {i}:")
        print(f"   - ID: {chunk.child_chunk_id}")
        print(f"   - Parent ID: {chunk.child_chunk_metadata.get('parent_chunk_id', 'N/A')}")
        print(f"   - Content length: {len(chunk.content)} chars")
        print(f"   - Content: {chunk.content[:200]}...")
    
    # Summary
    print("\n" + "="*80)
    print("✅ PIPELINE TEST COMPLETE")
    print("="*80)
    print(f" Summary:")
    print(f"   - File processed: {file_path.name}")
    print(f"   - Blocks extracted: {len(parse_result.structured_blocks)}")
    print(f"   - Parent chunks created: {len(parent_chunks)}")
    print(f"   - Child chunks created: {len(child_chunks)}")
    print(f"   - Images extracted: {len(parse_result.images)}")
    print(f"   - Strategy: docling-pptexcel")
    print("="*80)


if __name__ == "__main__":
    print("\n" + "╔" + "="*78 + "╗")
    print("║" + " "*20 + "FULL PPTX/XLSX PIPELINE TEST" + " "*30 + "║")
    print("╚" + "="*78 + "╝")
    
    # Test the new file with tables
    test_file = Path("_local_uploads/student_living_accommodation_sample_WITH_TABLE.pptx")
    content_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    
    if test_file.exists():
        test_full_pipeline(test_file, content_type)
    else:
        print(f"\n❌ Test file not found: {test_file}")
        print("\nAvailable files:")
        for f in Path("_local_uploads").glob("*.pptx"):
            print(f"   - {f.name}")
