import base64
import binascii
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Local imports
from app.service.rag.ingestion.text_extractor import extract_text
from app.service.rag.ingestion.chunker import split_parent_child_chunks
from app.service.rag.ingestion.chunk_polisher import polish_chunks
from app.service.rag.ingestion.docling_pdf_extractor import (
    get_pdf_ingestion_strategy,
    parse_pdf_with_docling_preview,
)
from app.vectordb.vectordb import upsert_documents

# Setup the API router
router = APIRouter()


# --- Data Models ---
class FileUpload(BaseModel):
    """
    Model for file upload.
    Expects base64-encoded file data.
    """

    fileName: str
    contentType: str
    data: str


class DoclingPreviewResponse(BaseModel):
    """
    Model for the response of the Docling preview endpoint.
    """
    status: str
    file_name: str
    artifact_run_id: str
    artifact_dir: str
    markdown_path: str
    stats: dict
    warnings: list[str]
    partial_failures: list[dict]


def _decode_base64(data: str) -> bytes:
    # Fail fast with a client error if the webhook payload is not valid base64.
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="invalid base64 payload")


def _extract_text_for_ingest(file: FileUpload, file_bytes: bytes) -> str:
    # Phase 2 rollout: only PDFs can switch to Docling; all other types stay on legacy extraction.
    if file.contentType == "application/pdf" and get_pdf_ingestion_strategy() == "docling":
        parse_result = parse_pdf_with_docling_preview(
            pdf_bytes=file_bytes,
            file_name=file.fileName,
        )
        print(
            "[ingest] file=%s strategy=docling run_id=%s converted_chunks=%s pictures=%s table_fallbacks=%s partial_failures=%s"
            % (
                file.fileName,
                parse_result.artifact_run_id,
                parse_result.stats.converted_chunks,
                parse_result.stats.pictures_extracted,
                parse_result.stats.table_fallback_images_extracted,
                parse_result.stats.partial_failure_chunks,
            )
        )
        # Feed Docling markdown into the existing chunker/upsert pipeline unchanged.
        return parse_result.markdown_text

    # Legacy path remains the default behavior (including PDFs unless env flag is enabled).
    text = extract_text(file.contentType, file_bytes)
    strategy = (
        get_pdf_ingestion_strategy() if file.contentType == "application/pdf" else "legacy"
    )
    print(f"[ingest] file={file.fileName} strategy={strategy}")
    return text


# --- Endpoint ---
@router.get("/health")
def ingest_health():
    """
    Health check endpoint for ingestion module.
    """

    return {"ingestion": "ok"}


@router.post("/webhook/preview", response_model=DoclingPreviewResponse)
async def ingest_webhook_preview(file: FileUpload):
    """
    Parse-only preview endpoint.
    Uses Docling for PDFs and writes markdown/image artifacts for manual inspection.
    """

    # v1 preview is intentionally PDF-only so we can validate Docling output safely.
    if file.contentType != "application/pdf":
        raise HTTPException(
            status_code=415,
            detail="Docling preview currently supports application/pdf only",
        )

    file_bytes = _decode_base64(file.data)
    try:
        # Parse and write local artifacts (markdown/images/manifest) without chunking or DB writes.
        parse_result = parse_pdf_with_docling_preview(
            pdf_bytes=file_bytes,
            file_name=file.fileName,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"docling preview failed: {exc}")

    return DoclingPreviewResponse(
        status="ok",
        file_name=file.fileName,
        artifact_run_id=parse_result.artifact_run_id,
        artifact_dir=parse_result.artifact_dir,
        markdown_path=parse_result.markdown_path,
        stats=parse_result.stats.model_dump(),
        warnings=parse_result.warnings,
        partial_failures=[item.model_dump() for item in parse_result.partial_failures],
    )


@router.post("/webhook")
async def ingest_webhook(file: FileUpload):
    """
    Main ingestion endpoint.

    Functionality:
    1. Extract text from the uploaded file.
    2. Split the text into Parent and Child chunks.
    3. Polish the Child chunks for better embedding quality.
    4. Upsert both Parent and Child chunks into their respective stores.
    """

    # Shared base64 decoding helper also validates payload format.
    file_bytes = _decode_base64(file.data)

    # 1. Extract text from the file bytes
    try:
        text = _extract_text_for_ingest(file, file_bytes)
    except ValueError as error:
        raise HTTPException(status_code=415, detail=str(error))
    except Exception:
        raise HTTPException(status_code=500, detail="text extraction failed")

    print(f"âœ… Successfully extracted text from {file.fileName}")

    # 2. Parent-Child Splitting
    # Returns lists of Pydantic models: [ParentChunkModel], [ChildChunkModel] (Refer to chunker.py)
    parent_chunks_models, child_chunks_models = split_parent_child_chunks(
        text, file_name=file.fileName, parent_target_chars=1500, child_max_chars=600
    )

    # 3. Preparation for Polishing: Convert Child Models to raw dictionaries
    # The polisher expects a List[Dict[str, Any]]. We use .model_dump() for conversion.
    child_chunks_dicts = [chunk.model_dump() for chunk in child_chunks_models]

    # 4. Polishing: Applied only to the embeddable child chunks' text
    polished_child_chunks = polish_chunks(child_chunks_dicts)

    parent_chunks_dicts = [chunk.model_dump() for chunk in parent_chunks_models]

    # 5. Upsert both Parent and Child chunks into their respective stores
    try:
        await upsert_documents(
            parent_chunks=parent_chunks_dicts, child_chunks=polished_child_chunks
        )
        print("âœ… Upserted all chunks into vector store.")
    except Exception:
        raise HTTPException(status_code=500, detail="upsert to vector store failed")
