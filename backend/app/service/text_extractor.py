from pathlib import Path
import fitz  # PyMuPDF
import docx

SUPPORTED = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}

def extract_text(content_type: str, data: bytes) -> str:
    if content_type == "application/pdf":
        p = Path("/tmp/_tmp.pdf"); p.write_bytes(data)
        doc = fitz.open(str(p))
        try:
            return "\n".join(page.get_text() for page in doc)
        finally:
            doc.close()
    elif content_type in {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        p = Path("/tmp/_tmp.docx"); p.write_bytes(data)
        d = docx.Document(str(p))
        return "\n".join(par.text for par in d.paragraphs)
    elif content_type.startswith("text/") or content_type == "text/plain":
        return data.decode("utf-8", errors="ignore")
    else:
        raise ValueError("Unsupported content_type")
