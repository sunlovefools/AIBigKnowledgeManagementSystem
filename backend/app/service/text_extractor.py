from pathlib import Path
import fitz  # PyMuPDF
import docx

# Supported content types (Currently includes PDF, Word, and plain text)
SUPPORTED = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}

def extract_text(contentType: str, data: bytes) -> str:
    """Extract text from file bytes based on content (File) type.
    Args:
        contentType (str): The MIME type of the file (e.g., "application/pdf").
        data (bytes): The raw bytes of the file.
    Return a huge string of all extracted text.
    """
    print(f"Extracting text for contentType: {contentType}")
    print
    # If the content type is PDF
    if contentType == "application/pdf":
        # Write bytes to a temporary file
        path = Path("/tmp/_tmp.pdf");  # Declare a temp path
        
        # Ensure the parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Open the file safely in binary write mode
        with open(path, "wb") as f:
            f.write(data)

        # Open the PDF with PyMuPDF
        document = fitz.open(str(path))
        try:
            # get_text() extracts text from each page
            # get_text() only selects text, ignores images, tables, etc. (Something to take note of for future)
            # get_text() return string of all text in one page (Loop through all pages so it return all the strings)
            # .join() combines all page strings into one big string with newlines in between
            return "\n".join(page.get_text() for page in document) # Return a huge string of all text in the PDF
        finally:
            # Ensure the document is closed after extraction
            document.close()
    # If the content type is Word document
    elif contentType in {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        # Write bytes to a temporary file
        path = Path("/tmp/_tmp.docx"); # Declare a temp path
        path.write_bytes(data)

        # Open the Word document with python-docx
        document = docx.Document(str(path))
        # Extract and join all paragraph texts
        return "\n".join(par.text for par in document.paragraphs)
    # If the content type is plain text
    elif contentType.startswith("text/") or contentType == "text/plain":
        return data.decode("utf-8", errors="ignore")
    else:
        raise ValueError("Unsupported contentType")