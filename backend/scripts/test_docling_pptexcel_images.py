"""Quick test to see if Docling extracts images from PowerPoint"""
from docling.document_converter import DocumentConverter
from pathlib import Path

pptx_path = Path("_local_uploads/student_living_accommodation_sample_WITH_TABLE.pptx")

print("Converting PowerPoint with Docling...")
converter = DocumentConverter()
result = converter.convert(str(pptx_path))

print("\nExporting to markdown...")
markdown = result.document.export_to_markdown()

print("\n" + "="*80)
print("MARKDOWN OUTPUT (first 3000 chars):")
print("="*80)
print(markdown[:3000])

# Check for image markers
has_images = "![" in markdown or "image" in markdown.lower()
has_tables = "|" in markdown

print("\n" + "="*80)
print("ANALYSIS:")
print("="*80)
print(f"Total markdown length: {len(markdown)} characters")
print(f"Contains images: {has_images}")
print(f"Contains tables: {has_tables}")
print(f"Image markers found: {markdown.count('![')}")
print(f"Table rows found: {len([line for line in markdown.split('\\n') if '|' in line])}")
