import io
import fitz  # PyMuPDF
import docx

# Supported content types (Currently includes PDF, Word, and plain text)
SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}

def extract_text(contentType: str, data: bytes) -> str:
    """
    Extract text from raw file bytes based on content (File) type.

    Args:
        contentType (str): The MIME type of the file (e.g., "application/pdf").
        data (bytes): The raw file content.

    Return:
      str: The extracted text content.
    """

    print(f"📄 Extracting text for contentType: {contentType} ({len(data)} bytes)")
    # If the content type is PDF
    if contentType == "application/pdf":
        return _extract_from_pdf(data)
    elif contentType in {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    }:
        return _extract_from_docx(data)
    elif contentType.startswith("text/") or contentType == "text/plain":
        return _extract_from_plain_text(data)
    else:
        raise ValueError(f"Unsupported content type for text extraction: {contentType}")
    
# --- Helper functions for different content types ---
def _extract_from_pdf(data: bytes) -> str:
    """
    Extract text from PDF bytes directly in memory using PyMuPDF.
    """

    with fitz.open(stream=data, filetype="pdf") as pdf_doc:
        text_pages = [] # List to hold text from each page
        for page in pdf_doc:
            text_pages.append(page.get_text())
        
        return "\n".join(text_pages) # Join all pages' text with newlines

def _extract_from_docx(data: bytes) -> str:
    """
    Extract text from Word document bytes directly in memory using python-docx.
    """

    file_stream = io.BytesIO(data)
    doc = docx.Document(file_stream)

    full_text = []

    for element in doc.element.body:
        
        # If the element is a Paragraph
        if isinstance(element, CT_P):
            para = Paragraph(element, doc)
            if para.text.strip():
                full_text.append(para.text)

        # If the element is a Table
        elif isinstance(element, CT_Tbl):
            table = Table(element, doc)
            for row in table.rows:
                # Clean and join cells with a pipe | 
                cells = [cell.text.strip() for cell in row.cells]
                full_text.append(" | ".join(cells))

    return "\n".join(full_text)

def _extract_from_plain_text(data: bytes) -> str:
    """
    Extract text from plain text bytes.
    """

    return data.decode('utf-8', errors='ignore')