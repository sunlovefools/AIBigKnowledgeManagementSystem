"""
API router for file modification and reconstruction operations.
Handles retrieving document lists and reconstructing files from chunks.
"""

import traceback
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import List

from app.service.modification_service import ReconstructionService

# Setup the API router
router = APIRouter()

# --- Data Models ---
class DocumentInfo(BaseModel):
    """Information about a reconstructed document."""
    id: str                    # Parent document ID
    fileName: str             # Original file name
    content: str              # Full reconstructed document content
    size: int                 # Character count
    chunks: int               # Number of chunks this document contains


class ModificationsResponse(BaseModel):
    """Response containing list of available documents for modification."""
    documents: List[DocumentInfo]
    total: int


# --- API Endpoints ---

@router.get("/health")
def modifications_health():
    """Health check endpoint for modifications module."""
    return {"modifications": "ok"}


@router.get("/list", response_model=ModificationsResponse)
async def get_modifiable_documents():
    """
    Retrieves all documents that have been ingested into the system.
    
    Returns a list of documents with their reconstructed content for modification.
    Each document is reconstructed from its parent chunks stored in the database.
    
    Returns:
        ModificationsResponse: List of documents with their metadata
    
    Raises:
        HTTPException: If document retrieval fails
    """
    
    print("📋 API: GET /api/modifications/list called")
    
    try:
        print("  → Calling ReconstructionService.get_all_documents()...")
        documents = await ReconstructionService.get_all_documents()
        
        print(f"  ✓ Retrieved {len(documents)} documents from service")
        
        # Convert to DocumentInfo models
        doc_list = [
            DocumentInfo(
                id=doc["id"],
                fileName=doc["fileName"],
                content=doc["content"],
                size=doc["size"],
                chunks=doc["chunks"],
            )
            for doc in documents
        ]
        
        print(f"  ✓ Successfully returning {len(doc_list)} DocumentInfo objects")
        return ModificationsResponse(
            documents=doc_list,
            total=len(doc_list)
        )
        
    except RuntimeError as e:
        print(f"  ❌ RuntimeError from service: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database service error: {str(e)}"
        )
    except Exception as e:
        print(f"  ❌ Unexpected error: {e}")
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve documents: {str(e)}"
        )


class UpdateDocumentRequest(BaseModel):
    """Request body for updating a document's content."""
    content: str              # New document content
    fileName: str             # Original file name (needed for re-chunking)


@router.put("/document/{document_id}", response_model=DocumentInfo)
async def update_document_content(document_id: str, request: UpdateDocumentRequest):
    """
    Updates the content of a specific document.

    This will:
    1. Delete old chunks from the database
    2. Re-chunk the new content
    3. Re-embed and store the new chunks

    Args:
        document_id: The parent document ID to update
        request: New content and file name

    Returns:
        DocumentInfo: The updated document info
    """

    print(f"📝 API: PUT /api/modifications/document/{document_id} called")

    if not request.content.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Content cannot be empty"
        )

    try:
        result = await ReconstructionService.update_document(
            parent_id=document_id,
            new_content=request.content,
            file_name=request.fileName
        )

        return DocumentInfo(
            id=result["id"],
            fileName=result["fileName"],
            content=result["content"],
            size=result["size"],
            chunks=result["chunks"],
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database service error: {str(e)}"
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update document: {str(e)}"
        )


@router.get("/document/{document_id}", response_model=DocumentInfo)
async def get_document_content(document_id: str):
    """
    Retrieves the full content of a specific document by ID.
    
    Args:
        document_id (str): The parent document ID
    
    Returns:
        DocumentInfo: The document with its full reconstructed content
    
    Raises:
        HTTPException: If document not found or retrieval fails
    """
    
    try:
        doc = await ReconstructionService.get_document_by_id(document_id)
        
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document with ID '{document_id}' not found"
            )
        
        return DocumentInfo(
            id=document_id,
            fileName="document",  # Not stored for individual retrieval
            content=doc["content"],
            size=doc["size"],
            chunks=0,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve document: {str(e)}"
        )
