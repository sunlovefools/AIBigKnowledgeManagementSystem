"""
Docling-based extraction for PowerPoint and Excel files (PPTX/XLSX).

Purpose:
- Unified extraction for PPTX and XLSX files using Docling
- Returns structured blocks compatible with the Docling chunking pipeline
- Follows the same design pattern as docling_pdf_extractor.py
- Extracts images from PPTX/XLSX documents (similar to PDF image extraction)

Supported formats:
- PowerPoint (.pptx): application/vnd.openxmlformats-officedocument.presentationml.presentation
- Excel (.xlsx): application/vnd.openxmlformats-officedocument.spreadsheetml.sheet

Flow:
1. Convert PPTX/XLSX to Docling Document using DocumentConverter (text/tables)
2. Extract images separately using python-pptx / openpyxl (Docling limitation workaround)
3. Export document to markdown (structured text format)
4. Parse markdown into structured blocks for chunking
5. Inject image blocks with UUID markers (like PDF pipeline)
6. Return structured blocks compatible with docling_chunker.py
"""

from __future__ import annotations

import io
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.core.id_utils import generate_uuid_v6
from app.service.rag.ingestion.docling import table_image_vlm
from app.service.rag.ingestion.docling.models import (
    DoclingStructuredBlock,
    ExtractedImageArtifact,
)
from app.service.rag.ingestion.docling.storage import s3_upload

# MIME types for PowerPoint/Excel documents
SUPPORTED_PPTEXCEL_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
}


class DoclingPptExcelParseResult:
    """
    Result from parsing a PowerPoint or Excel file with Docling.
    
    Similar to DoclingParseResult but simplified for PPTX/XLSX documents.
    Now includes image extraction like PDF pipeline.
    """
    
    def __init__(
        self,
        structured_blocks: list[DoclingStructuredBlock],
        markdown_content: str,
        warnings: list[str],
        file_id: str,
        file_name: str,
        images: list[ExtractedImageArtifact] | None = None,
    ):
        self.structured_blocks = structured_blocks
        self.markdown_content = markdown_content
        self.warnings = warnings
        self.file_id = file_id
        self.file_name = file_name
        self.images = images or []
        self.artifact_dir: Path | None = None
        self.artifact_run_id: str | None = None
        self.partial_failures: list[str] = []


def _extract_images_from_pptx(
    file_bytes: bytes,
    artifact_dir: Path | None,
    file_name: str,
    file_id: str,
    warnings: list[str],
) -> tuple[list[ExtractedImageArtifact], dict[int, list[str]], dict[str, int]]:
    """
    Extract images from PowerPoint file using python-pptx.
    
    Returns:
        - List of ExtractedImageArtifact objects
        - Dict mapping slide number to list of image UUIDs on that slide
        - Dict with S3 upload statistics (failed, uploaded, skipped counts)
    """
    images: list[ExtractedImageArtifact] = []
    slide_images: dict[int, list[str]] = {}  # slide_no -> [image_uuids]
    s3_stats = {"failed": 0, "uploaded": 0, "skipped": 0}
    
    if not artifact_dir:
        return images, slide_images, s3_stats
    
    try:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE
    except ImportError:
        warnings.append(
            "python-pptx not installed - cannot extract PowerPoint images. "
            "Install with: pip install python-pptx"
        )
        return images, slide_images, s3_stats
    
    try:
        prs = Presentation(io.BytesIO(file_bytes))
        picture_counter = 0
        
        for slide_no, slide in enumerate(prs.slides, start=1):
            slide_image_uuids: list[str] = []
            
            for shape in slide.shapes:
                # Check if shape is a picture
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    picture_counter += 1
                    
                    try:
                        # Extract image bytes
                        image_bytes = shape.image.blob
                        
                        # Generate UUID for this image
                        image_uuid = generate_uuid_v6()
                        
                        # Save image to artifact directory
                        image_name = f"{image_uuid}.png"
                        image_path = artifact_dir / "images" / image_name
                        image_path.parent.mkdir(parents=True, exist_ok=True)
                        image_path.write_bytes(image_bytes)
                        
                        # Create artifact
                        artifact = ExtractedImageArtifact(
                            kind="picture",
                            image_uuid=image_uuid,
                            file_name=image_name,
                            file_path=str(image_path),
                            page_no=slide_no,
                            picture_index=picture_counter,
                        )
                        
                        # Upload to S3 (same as PDF pipeline)
                        artifact = s3_upload.upload_image_artifact_to_s3(
                            artifact,
                            source_file_name=file_name,
                            file_id=file_id,
                        )
                        
                        if artifact.s3_upload_status == "failed":
                            s3_stats["failed"] += 1
                            warnings.append(
                                f"Failed to upload PowerPoint image_uuid={artifact.image_uuid} to S3: {artifact.s3_error}"
                            )
                        elif artifact.s3_upload_status == "uploaded":
                            s3_stats["uploaded"] += 1
                        elif artifact.s3_upload_status == "skipped":
                            s3_stats["skipped"] += 1
                        
                        images.append(artifact)
                        slide_image_uuids.append(image_uuid)
                        
                    except Exception as e:
                        warnings.append(
                            f"Failed to extract image #{picture_counter} from slide {slide_no}: {e}"
                        )
            
            if slide_image_uuids:
                slide_images[slide_no] = slide_image_uuids
        
        print(f"[docling-pptexcel] Extracted {len(images)} images from PowerPoint")
        print(f"[docling-pptexcel] S3 upload: {s3_stats['uploaded']} uploaded, {s3_stats['skipped']} skipped, {s3_stats['failed']} failed")
        
    except Exception as e:
        warnings.append(f"Failed to extract PowerPoint images: {e}")
    
    return images, slide_images, s3_stats


def _extract_images_from_xlsx(
    file_bytes: bytes,
    artifact_dir: Path | None,
    file_name: str,
    file_id: str,
    warnings: list[str],
) -> tuple[list[ExtractedImageArtifact], dict[int, list[str]], dict[str, int]]:
    """
    Extract images from Excel file using openpyxl.
    
    Returns:
        - List of ExtractedImageArtifact objects
        - Dict mapping sheet number to list of image UUIDs on that sheet
        - Dict with S3 upload statistics (failed, uploaded, skipped counts)
    """
    images: list[ExtractedImageArtifact] = []
    sheet_images: dict[int, list[str]] = {}  # sheet_no -> [image_uuids]
    s3_stats = {"failed": 0, "uploaded": 0, "skipped": 0}
    
    if not artifact_dir:
        return images, sheet_images, s3_stats
    
    try:
        import openpyxl
    except ImportError:
        warnings.append(
            "openpyxl not installed - cannot extract Excel images. "
            "Install with: pip install openpyxl"
        )
        return images, sheet_images, s3_stats
    
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes))
        picture_counter = 0
        
        for sheet_no, sheet_name in enumerate(wb.sheetnames, start=1):
            sheet = wb[sheet_name]
            sheet_image_uuids: list[str] = []
            
            # Check if sheet has images
            if hasattr(sheet, '_images') and sheet._images:
                for img in sheet._images:
                    picture_counter += 1
                    
                    try:
                        # Extract image bytes
                        image_bytes = img._data()
                        
                        # Generate UUID for this image
                        image_uuid = generate_uuid_v6()
                        
                        # Save image to artifact directory
                        image_name = f"{image_uuid}.png"
                        image_path = artifact_dir / "images" / image_name
                        image_path.parent.mkdir(parents=True, exist_ok=True)
                        image_path.write_bytes(image_bytes)
                        
                        # Create artifact
                        artifact = ExtractedImageArtifact(
                            kind="picture",
                            image_uuid=image_uuid,
                            file_name=image_name,
                            file_path=str(image_path),
                            page_no=sheet_no,
                            picture_index=picture_counter,
                        )
                        
                        # Upload to S3 (same as PDF pipeline)
                        artifact = s3_upload.upload_image_artifact_to_s3(
                            artifact,
                            source_file_name=file_name,
                            file_id=file_id,
                        )
                        
                        if artifact.s3_upload_status == "failed":
                            s3_stats["failed"] += 1
                            warnings.append(
                                f"Failed to upload Excel image_uuid={artifact.image_uuid} to S3: {artifact.s3_error}"
                            )
                        elif artifact.s3_upload_status == "uploaded":
                            s3_stats["uploaded"] += 1
                        elif artifact.s3_upload_status == "skipped":
                            s3_stats["skipped"] += 1
                        
                        images.append(artifact)
                        sheet_image_uuids.append(image_uuid)
                        
                    except Exception as e:
                        warnings.append(
                            f"Failed to extract image #{picture_counter} from sheet '{sheet_name}': {e}"
                        )
            
            if sheet_image_uuids:
                sheet_images[sheet_no] = sheet_image_uuids
        
        print(f"[docling-pptexcel] Extracted {len(images)} images from Excel")
        print(f"[docling-pptexcel] S3 upload: {s3_stats['uploaded']} uploaded, {s3_stats['skipped']} skipped, {s3_stats['failed']} failed")
        
    except Exception as e:
        warnings.append(f"Failed to extract Excel images: {e}")
    
    return images, sheet_images, s3_stats


def _might_contain_table(image_artifact: ExtractedImageArtifact) -> bool:
    """
    Heuristic to determine if an image might contain a table.
    
    For now, we check if the image file exists and has a reasonable size.
    In the future, this could be enhanced with image analysis (OCR, ML models).
    
    Args:
        image_artifact: The extracted image artifact
        
    Returns:
        True if the image might contain a table, False otherwise
    """
    try:
        from PIL import Image
        
        image_path = Path(image_artifact.file_path)
        if not image_path.exists():
            return False
        
        # Open image to get dimensions
        with Image.open(image_path) as img:
            width, height = img.size
            
            # Heuristic: consider image might contain table if it's large enough
            # Similar to PDF pipeline: width * height > 50000
            return (width * height) > 50000
            
    except Exception:
        # If we can't analyze the image, assume it might contain a table
        # Better to run VLM unnecessarily than to miss a table
        return True


def is_pptexcel_document(content_type: str) -> bool:
    """
    Check if a content type is a supported PowerPoint/Excel document format.
    
    Args:
        content_type: MIME type string (e.g., "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        
    Returns:
        True if the content type is PPTX or XLSX, False otherwise
    """
    return content_type in SUPPORTED_PPTEXCEL_MIME_TYPES


def _parse_markdown_to_blocks(
    markdown_content: str,
    file_name: str,
    page_images: dict[int, list[str]] | None = None,
    table_image_summaries: dict[str, str] | None = None,
) -> list[DoclingStructuredBlock]:
    """
    Parse Docling markdown output into structured blocks for chunking.
    
    Strategy:
    - Split by markdown headers (# Header, ## Header, etc.)
    - Detect tables (lines with |...|)
    - Detect lists (lines starting with - or *)
    - Inject image blocks with UUID markers (like PDF pipeline)
    - Inject VLM summaries for images containing tables
    - Classify remaining content as text blocks
    
    Args:
        markdown_content: Markdown text from Docling export_to_markdown()
        file_name: Original file name for metadata
        page_images: Dict mapping page/slide number to list of image UUIDs (optional)
        table_image_summaries: Dict mapping image UUID to VLM extracted table summary (optional)
        
    Returns:
        List of DoclingStructuredBlock objects ready for chunking
    """
    
    blocks: list[DoclingStructuredBlock] = []
    lines = markdown_content.split("\n")
    
    current_block_lines: list[str] = []
    current_block_type = "text"
    block_index = 0
    current_page_no = 1  # PPTX/XLSX don't have pages, using logical pages (slides/sheets)
    page_images = page_images or {}
    table_image_summaries = table_image_summaries or {}
    
    # For tracking slide numbers in PowerPoint (## Slide N pattern)
    slide_number_pattern = re.compile(r"^##\s+Slide\s+(\d+)", re.IGNORECASE)
    
    # For tracking sheet names in Excel (## Sheet: Name pattern)
    sheet_pattern = re.compile(r"^##\s+Sheet:\s+(.+)", re.IGNORECASE)
    
    def _flush_current_block():
        """Helper to finalize and append the current block."""
        nonlocal blocks, current_block_lines, current_block_type, block_index
        
        if not current_block_lines:
            return
        
        content = "\n".join(current_block_lines).strip()
        if not content:
            current_block_lines = []
            return
        
        # Create structured block
        block = DoclingStructuredBlock(
            block_index=block_index,
            block_type=current_block_type,
            content=content,
            page_no=current_page_no,
            is_table_image=False,
            table_image_uuid=None,
        )
        blocks.append(block)
        block_index += 1
        current_block_lines = []
    
    def _inject_image_blocks_for_page(page_no: int):
        """Inject picture blocks for images on a specific page/slide, with VLM summaries if available."""
        nonlocal blocks, block_index
        
        image_uuids = page_images.get(page_no, [])
        for image_uuid in image_uuids:
            # Create marker like PDF pipeline: <!-- image-uuid: xxx -->
            image_marker = f"<!-- image-uuid: {image_uuid} -->"
            
            # Check if this image has a VLM summary (table extraction)
            vlm_summary = table_image_summaries.get(image_uuid)
            
            if vlm_summary:
                # This image contains a table - create a table-image block
                print(f"[docling-pptexcel] Injecting table-image block with VLM summary for {image_uuid}")
                
                # Combine image marker with VLM summary
                combined_content = f"> **Table (image)**: Table detected in image.\n> {image_marker}\n\n{vlm_summary}"
                
                table_image_block = DoclingStructuredBlock(
                    block_index=block_index,
                    block_type="table",
                    content=combined_content,
                    page_no=page_no,
                    is_table_image=True,
                    table_image_uuid=image_uuid,
                )
                blocks.append(table_image_block)
                block_index += 1
            else:
                # Regular picture without table content
                picture_block = DoclingStructuredBlock(
                    block_index=block_index,
                    block_type="picture",
                    content=image_marker,
                    page_no=page_no,
                    is_table_image=False,
                    table_image_uuid=None,
                )
                blocks.append(picture_block)
                block_index += 1
                print(f"[docling-pptexcel] Injected picture block for image {image_uuid} on page {page_no}")
    
    for line in lines:
        stripped_line = line.strip()
        
        # Skip completely empty lines between blocks
        if not stripped_line and not current_block_lines:
            continue
        
        # Detect markdown headers (titles/sections)
        if stripped_line.startswith("#"):
            # Flush previous block before starting a header
            _flush_current_block()
            
            # Check if this is a PowerPoint slide marker
            slide_match = slide_number_pattern.match(stripped_line)
            if slide_match:
                current_page_no = int(slide_match.group(1))
            
            # Check if this is an Excel sheet marker
            sheet_match = sheet_pattern.match(stripped_line)
            if sheet_match:
                current_page_no += 1  # Increment logical page for sheets
            
            # Start a new header block
            current_block_type = "header"
            # Remove markdown # symbols for cleaner content
            header_text = stripped_line.lstrip("#").strip()
            current_block_lines.append(header_text)
            _flush_current_block()
            
            # After flushing header, inject any images for this page/slide
            _inject_image_blocks_for_page(current_page_no)
            
            continue
        
        # Detect markdown tables (lines with |...|)
        if "|" in stripped_line and stripped_line.count("|") >= 2:
            # If we were building a different type of block, flush it
            if current_block_type != "table":
                _flush_current_block()
                current_block_type = "table"
            
            # Skip table separator lines (|---|---|)
            if re.match(r"^\|[\s\-:]+\|", stripped_line):
                continue
            
            current_block_lines.append(stripped_line)
            continue
        
        # Detect markdown lists (lines starting with - or *)
        if stripped_line.startswith(("-", "*", "•")) or re.match(r"^\d+\.\s", stripped_line):
            # If we were building a different type of block, flush it
            if current_block_type != "list":
                _flush_current_block()
                current_block_type = "list"
            
            current_block_lines.append(stripped_line)
            continue
        
        # Otherwise, it's regular text
        if current_block_type not in ("text", "list", "table"):
            _flush_current_block()
            current_block_type = "text"
        
        # If table or list ended, flush and start text
        if not stripped_line and current_block_type in ("table", "list"):
            _flush_current_block()
            current_block_type = "text"
        
        # Add to current block
        if stripped_line or current_block_lines:  # Keep blank lines within blocks
            current_block_lines.append(line)
    
    # Flush any remaining block
    _flush_current_block()
    
    # Inject any remaining images that weren't injected during header processing
    # This handles cases where images exist on slides without explicit headers
    all_pages_with_images = set(page_images.keys())
    for page_no in sorted(all_pages_with_images):
        _inject_image_blocks_for_page(page_no)
    
    print(f"[docling-pptexcel] Parsed {len(blocks)} blocks from markdown")
    return blocks


def parse_pptexcel_with_docling(
    file_bytes: bytes,
    file_name: str,
    content_type: str,
    file_id: str | None = None,
) -> DoclingPptExcelParseResult:
    """
    Parse PowerPoint/Excel file with Docling and extract text/tables/images.
    
    This is the main entry point for PPTX/XLSX document ingestion, similar to
    parse_pdf_with_docling_preview() for PDFs.
    
    Args:
        file_bytes: Raw file content bytes
        file_name: Original filename (for logging and metadata)
        content_type: MIME type (must be PPTX or XLSX)
        file_id: Optional unique identifier for this file
        
    Returns:
        DoclingPptExcelParseResult with structured blocks ready for chunking
        
    Raises:
        ValueError: If content type is unsupported or file_bytes is empty
        ImportError: If Docling library is not installed
        Exception: For Docling processing errors
    """
    
    # Validation
    if not file_bytes:
        raise ValueError("empty file payload")
    
    if not is_pptexcel_document(content_type):
        raise ValueError(
            f"Unsupported content type for Docling PPTX/XLSX extraction: {content_type}. "
            f"Supported types: {', '.join(SUPPORTED_PPTEXCEL_MIME_TYPES)}"
        )
    
    # Determine file type for logging
    file_type = "PowerPoint" if "presentation" in content_type else "Excel"
    print(f"[docling-pptexcel] Processing {file_type} file: {file_name} ({len(file_bytes)} bytes)")
    
    resolved_file_id = (file_id or generate_uuid_v6()).strip()
    warnings: list[str] = []
    
    # Import Docling (lazy import to avoid startup overhead if not used)
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as e:
        raise ImportError(
            f"Docling library not installed. Install with: pip install docling. Error: {e}"
        )
    
    try:
        # Step 1: Convert PPTX/XLSX file to Docling Document
        print(f"[docling-pptexcel] Converting {file_type} with Docling...")
        
        # Docling's DocumentConverter requires a file path, not a BytesIO stream
        # Create a temporary file with the correct extension
        import tempfile
        import os
        
        # Get file extension from filename
        extension = Path(file_name).suffix  # e.g., ".pptx" or ".xlsx"
        
        # Create temporary file
        with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as temp_file:
            temp_file.write(file_bytes)
            temp_file_path = temp_file.name
        
        try:
            # Convert using the temporary file path
            converter = DocumentConverter()
            result = converter.convert(temp_file_path)
            
            print(f"[docling-pptexcel] Conversion successful")
            
        finally:
            # Clean up temporary file
            try:
                os.unlink(temp_file_path)
            except Exception as e:
                print(f"[docling-pptexcel] Warning: Could not delete temp file {temp_file_path}: {e}")
        
        # Step 2: Export to markdown
        print(f"[docling-pptexcel] Exporting to markdown...")
        markdown_content = result.document.export_to_markdown()
        print(f"[docling-pptexcel] Exported {len(markdown_content)} characters of markdown")
        
        # Step 2.5: Add file name and sheet/slide structure headers
        # Docling doesn't include Excel sheet names or file context in markdown export
        # We extract metadata and inject as headers for better structure
        if file_type == "Excel":
            try:
                import openpyxl
                print(f"[docling-pptexcel] Adding Excel sheet names as headers...")
                
                # Get sheet names from original file
                wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
                sheet_names = wb.sheetnames
                num_sheets = len(sheet_names)
                
                # Remove Docling's <!-- image --> markers (they mark sheet boundaries but aren't real images)
                # We'll add our own image markers later when we actually have images
                markdown_content = markdown_content.replace("<!-- image -->", "")
                
                if num_sheets > 1:
                    # Split markdown by sheet boundaries
                    # Docling exports sheets sequentially, separated by empty lines
                    # We need to intelligently split and insert headers inline
                    
                    # Split by double newlines to find major content blocks
                    content_blocks = markdown_content.split("\n\n")
                    
                    # Rebuild markdown with sheet headers inserted at appropriate positions
                    # Heuristic: distribute blocks roughly equally among sheets
                    blocks_per_sheet = len(content_blocks) // num_sheets
                    
                    restructured_content = f"# {file_name}\n\n"
                    block_index = 0
                    
                    for sheet_idx, sheet_name in enumerate(sheet_names):
                        # Add sheet header
                        restructured_content += f"## {sheet_name}\n\n"
                        
                        # Add blocks for this sheet
                        if sheet_idx == len(sheet_names) - 1:
                            # Last sheet gets all remaining blocks
                            sheet_blocks = content_blocks[block_index:]
                        else:
                            sheet_blocks = content_blocks[block_index:block_index + blocks_per_sheet]
                        
                        restructured_content += "\n\n".join(block for block in sheet_blocks if block.strip())
                        restructured_content += "\n\n"
                        
                        block_index += blocks_per_sheet
                    
                    markdown_content = restructured_content
                    print(f"[docling-pptexcel] Restructured markdown with headers for {num_sheets} Excel sheets: {sheet_names}")
                else:
                    # Single sheet - add filename as main header and sheet name as subheader
                    markdown_content = f"# {file_name}\n\n## {sheet_names[0]}\n\n" + markdown_content
                    print(f"[docling-pptexcel] Added headers for single Excel sheet: {sheet_names[0]}")
                    
            except Exception as e:
                warnings.append(f"Could not add Excel sheet names to markdown: {e}")
                print(f"[docling-pptexcel] Warning: Could not add sheet names: {e}")
        
        elif file_type == "PowerPoint":
            # Add filename as main header for PowerPoint files
            # Remove Docling's empty <!-- image --> markers (we add our own later)
            markdown_content = markdown_content.replace("<!-- image -->", "")
            markdown_content = f"# {file_name}\n\n" + markdown_content
            print(f"[docling-pptexcel] Added filename header for PowerPoint")
        
        # Step 3: Extract images (Docling limitation workaround)
        print(f"[docling-pptexcel] Extracting images from {file_type}...")
        from app.service.rag.ingestion.docling.storage import local_artifacts_store
        
        # Prepare artifact directory for images (like PDF pipeline)
        run_id, artifact_dir, markdown_path = local_artifacts_store.prepare_docling_preview_artifact_dir(
            file_name=file_name,
            artifact_root=None,  # Use default artifacts root
        )
        
        # Extract images based on file type
        if file_type == "PowerPoint":
            images, page_images, s3_stats = _extract_images_from_pptx(
                file_bytes=file_bytes,
                artifact_dir=artifact_dir,
                file_name=file_name,
                file_id=resolved_file_id,
                warnings=warnings,
            )
        else:  # Excel
            images, page_images, s3_stats = _extract_images_from_xlsx(
                file_bytes=file_bytes,
                artifact_dir=artifact_dir,
                file_name=file_name,
                file_id=resolved_file_id,
                warnings=warnings,
            )
        
        print(f"[docling-pptexcel] Extracted {len(images)} images from {file_type}")
        
        # Step 3.5: Process images with VLM for table detection and extraction
        table_image_vlm_jobs: list[table_image_vlm.TableImageVlmJob] = []
        table_image_summaries: dict[str, str] = {}  # image_uuid -> VLM summary text
        
        # Initialize VLM runtime if enabled
        table_image_vlm_runtime = (
            table_image_vlm.build_table_image_vlm_runtime(
                artifact_dir=artifact_dir,
                warnings=warnings,
            )
            if artifact_dir is not None
            else None
        )
        
        if table_image_vlm_runtime is not None and images:
            print(f"[docling-pptexcel] Table image VLM enabled - analyzing {len(images)} images for tables...")
            
            # Create thread pool executor for VLM processing
            table_image_vlm_executor = ThreadPoolExecutor(
                max_workers=table_image_vlm_runtime.max_workers,
                thread_name_prefix="table-vlm-pptexcel",
            )
            
            try:
                # Create VLM jobs for images that might contain tables
                table_counter = 0
                for image_artifact in images:
                    if _might_contain_table(image_artifact):
                        table_counter += 1
                        
                        # Create summary placeholder that will be replaced with VLM result
                        summary_placeholder = table_image_vlm.table_image_vlm_summary_placeholder(
                            image_artifact.image_uuid
                        )
                        
                        # Create VLM job
                        job = table_image_vlm.TableImageVlmJob(
                            image_artifact=image_artifact,
                            table_index=table_counter,
                            page_no=image_artifact.page_no,
                            block_index=0,  # Will update during markdown injection
                            summary_placeholder=summary_placeholder,
                            output_dir=table_image_vlm.table_image_vlm_output_dir(
                                artifact_dir,
                                table_index=table_counter,
                                image_uuid=image_artifact.image_uuid,
                            ),
                            json_rel_path=table_image_vlm.table_image_vlm_json_rel_path(
                                table_index=table_counter,
                                image_uuid=image_artifact.image_uuid,
                            ),
                        )
                        
                        table_image_vlm_jobs.append(job)
                
                if table_image_vlm_jobs:
                    print(f"[docling-pptexcel] Created {len(table_image_vlm_jobs)} VLM jobs for potential table images")
                    
                    # Submit all VLM jobs for processing (force=True to submit immediately without context)
                    # For PPTX, we submit all jobs immediately since we don't have markdown_parts context
                    for job in table_image_vlm_jobs:
                        if not job.submitted:
                            try:
                                job.context_before = ""
                                job.context_after = ""
                                job.future = table_image_vlm_executor.submit(
                                    table_image_vlm._process_table_image_vlm_job,
                                    table_image_vlm_runtime,
                                    job,
                                    context_before="",
                                    context_after="",
                                )
                                job.submitted = True
                            except Exception as exc:
                                warnings.append(
                                    f"Failed to submit table-image VLM job image_uuid={job.image_artifact.image_uuid}: {exc}"
                                )
                    
                    # Wait for all VLM jobs to complete and extract results
                    # Note: We don't call finalize_table_image_vlm_jobs because it tries to replace
                    # placeholders in markdown_parts, which we don't use for PPTX.
                    # Instead, we manually wait for futures and extract VLM summaries.
                    print(f"[docling-pptexcel] Waiting for VLM jobs to complete...")
                    
                    for job in table_image_vlm_jobs:
                        if job.future is not None:
                            try:
                                result = job.future.result()
                                job.result = result
                                
                                if result.summary_text:
                                    table_image_summaries[job.image_artifact.image_uuid] = result.summary_text
                                    print(f"[docling-pptexcel] ✓ VLM extracted table from image {job.image_artifact.image_uuid}")
                                    
                                if result.errors:
                                    for error in result.errors:
                                        warnings.append(
                                            f"Table-image VLM issue image_uuid={job.image_artifact.image_uuid}: {error}"
                                        )
                            except Exception as exc:
                                warnings.append(
                                    f"VLM job failed for image_uuid={job.image_artifact.image_uuid}: {exc}"
                                )
                    
                    # Create aggregate results file (similar to finalize but without markdown replacement)
                    if artifact_dir:
                        aggregate_entries = []
                        for job in table_image_vlm_jobs:
                            result = job.result
                            aggregate_entries.append({
                                "image_uuid": job.image_artifact.image_uuid,
                                "table_index": job.table_index,
                                "page_no": job.page_no,
                                "image_file_name": job.image_artifact.file_name,
                                "image_file_path": job.image_artifact.file_path,
                                "json_rel_path": job.json_rel_path,
                                "json_path": None if result is None else result.json_path,
                                "summary_path": None if result is None else result.summary_path,
                                "json_ok": False if result is None else result.json_ok,
                                "summary_ok": False if result is None else result.summary_ok,
                                "summary_text": None if result is None else result.summary_text,
                                "errors": [] if result is None else result.errors,
                            })
                        
                        # Save aggregate results
                        import json
                        (artifact_dir / "table_image_vlm_results.json").write_text(
                            json.dumps({"tables": aggregate_entries}, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
                    
                    print(f"[docling-pptexcel] VLM processing complete: {len(table_image_summaries)} tables extracted from images")
                else:
                    print(f"[docling-pptexcel] No images detected as potential tables")
                    
            finally:
                # Shutdown executor
                table_image_vlm_executor.shutdown(wait=True)
        else:
            if not table_image_vlm_runtime:
                print(f"[docling-pptexcel] Table image VLM disabled (check OPENROUTER_API_KEY)")
            elif not images:
                print(f"[docling-pptexcel] No images to analyze for tables")
        
        # Step 4: Parse markdown into structured blocks (with image injection)
        print(f"[docling-pptexcel] Parsing markdown into structured blocks...")
        structured_blocks = _parse_markdown_to_blocks(
            markdown_content=markdown_content,
            file_name=file_name,
            page_images=page_images,  # ← Pass image mapping for injection
            table_image_summaries=table_image_summaries,  # ← Pass VLM summaries for table images
        )
        
        if not structured_blocks:
            warnings.append(
                f"Docling produced markdown but no structured blocks were extracted for {file_name}"
            )
        
        print(f"[docling-pptexcel] Successfully created {len(structured_blocks)} structured blocks")
        
        # Step 4.5: Save markdown with image markers to document.md (like PDF pipeline)
        if artifact_dir and markdown_path:
            print(f"[docling-pptexcel] Saving markdown to {markdown_path}...")
            
            # Build enhanced markdown with clickable image links (like PDF does)
            enhanced_markdown_lines = []
            enhanced_markdown_lines.append(f"# {file_name}\n")
            enhanced_markdown_lines.append(markdown_content)
            
            # Add image references at the end
            if images:
                enhanced_markdown_lines.append("\n\n---\n")
                enhanced_markdown_lines.append(f"## Images ({len(images)} extracted)\n")
                for img in images:
                    rel_path = f"images/{img.file_name}"
                    enhanced_markdown_lines.append(f"\n### Image {img.page_no}-{img.picture_index}")
                    enhanced_markdown_lines.append(f"- UUID: `{img.image_uuid}`")
                    enhanced_markdown_lines.append(f"- Kind: {img.kind}")
                    enhanced_markdown_lines.append(f"- Page: {img.page_no}")
                    
                    # Check if this image has VLM table extraction
                    vlm_summary = table_image_summaries.get(img.image_uuid)
                    if vlm_summary:
                        enhanced_markdown_lines.append(f"- **Table detected** (VLM extracted)")
                        enhanced_markdown_lines.append(f"\n> {vlm_summary}\n")
                    
                    enhanced_markdown_lines.append(f"\n![{img.file_name}]({rel_path})")
                    enhanced_markdown_lines.append(f"\n<!-- image-uuid: {img.image_uuid} -->\n")
            
            # Save enhanced markdown to document.md
            enhanced_markdown = "\n".join(enhanced_markdown_lines)
            markdown_path.write_text(enhanced_markdown, encoding="utf-8")
            print(f"[docling-pptexcel] ✓ Saved markdown document ({len(enhanced_markdown)} chars)")
        
        # Step 5: Build result
        parse_result = DoclingPptExcelParseResult(
            structured_blocks=structured_blocks,
            markdown_content=markdown_content,
            warnings=warnings,
            file_id=resolved_file_id,
            file_name=file_name,
            images=images,  # ← Include extracted images
        )
        
        # Set artifact directory for chunker to use
        parse_result.artifact_dir = artifact_dir
        parse_result.artifact_run_id = run_id
        
        return parse_result
        
    except Exception as exc:
        error_msg = f"Docling {file_type} processing failed for {file_name}: {exc}"
        print(f"[docling-pptexcel] ERROR: {error_msg}")
        raise Exception(error_msg) from exc


def get_pptexcel_ingestion_strategy() -> str:
    """
    Determine the PowerPoint/Excel document ingestion strategy.
    
    Currently always returns "docling" for PowerPoint and Excel files.
    This function exists for consistency with PDF ingestion strategy pattern.
    
    Returns:
        "docling" - Use Docling-based extraction
    """
    return "docling"


__all__ = [
    "SUPPORTED_PPTEXCEL_MIME_TYPES",
    "DoclingPptExcelParseResult",
    "is_pptexcel_document",
    "parse_pptexcel_with_docling",
    "get_pptexcel_ingestion_strategy",
]
