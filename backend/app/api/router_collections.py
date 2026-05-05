"""API router for logical user collections (folders)."""

from __future__ import annotations

import traceback

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.dependencies import get_current_user
from app.service.collection.collection_service import (
    CollectionConflictError,
    CollectionNotFoundError,
    CollectionService,
    CollectionServiceError,
    ProtectedCollectionError,
)

router = APIRouter()


class CollectionSummary(BaseModel):
    """Summary information about a user collection (folder)."""
    collectionId: str
    name: str
    isDefault: bool
    fileCount: int
    createdAt: str
    updatedAt: str


class CollectionListResponse(BaseModel):
    """Response model for listing user collections."""
    collections: list[CollectionSummary]
    total: int


class CreateCollectionRequest(BaseModel):
    """Request model for creating a new user collection."""
    name: str


class RenameCollectionRequest(BaseModel):
    """Request model for renaming a user collection."""
    newName: str


class DeleteCollectionResponse(BaseModel):
    """Response model for deleting a user collection."""
    collectionId: str
    name: str
    deletedFiles: int
    deletedParentChunks: int
    deletedChildChunks: int
    warnings: list[str]


def _to_summary(item: dict) -> CollectionSummary:
    """Helper function to convert a collection item dict to a CollectionSummary model."""
    return CollectionSummary(
        collectionId=str(item.get("collection_id") or ""),
        name=str(item.get("name") or ""),
        isDefault=bool(item.get("is_default", False)),
        fileCount=int(item.get("file_count", 0) or 0),
        createdAt=str(item.get("created_at") or ""),
        updatedAt=str(item.get("updated_at") or ""),
    )

# -- API Endpoints --
# A simple health check endpoint for the collections router
@router.get("/health")
def collections_health():
    return {"collections": "ok"}

# Endpoint to list all collections for the current user
@router.get("", response_model=CollectionListResponse)
async def list_collections(current_user: dict = Depends(get_current_user)):
    user_id = current_user.get("sub") # Get user ID from the authentication token

    # Get the list of collections for the user and return as response
    try:
        collections = await CollectionService.list_collections(user_id)

        # Convert raw collection items to CollectionSummary models
        summaries = [_to_summary(item) for item in collections]
        return CollectionListResponse(collections=summaries, total=len(summaries))
    except CollectionServiceError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except Exception as error:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list collections: {error}",
        )

# Endpoint to get details of a specific collection by ID
@router.get("/{collection_id}", response_model=CollectionSummary)
async def get_collection(collection_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user.get("sub") # Get user ID from the authentication token

    # Fetch the collection details and return as response
    try:
        collection = await CollectionService.get_collection(user_id, collection_id)
        return _to_summary(collection) # Convert raw collection item to CollectionSummary model
    except CollectionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except CollectionServiceError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except Exception as error:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch collection: {error}",
        )

# Endpoint to create a new collection for the current user
@router.post("", response_model=CollectionSummary)
async def create_collection(
    payload: CreateCollectionRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("sub") # Get user ID from the authentication token

    # Create the collection and return the created collection as response
    try:
        collection = await CollectionService.create_collection(user_id, payload.name)
        return _to_summary(collection)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except CollectionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except CollectionServiceError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except Exception as error:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create collection: {error}",
        )

# Endpoint to rename an existing collection for the current user
@router.patch("/{collection_id}", response_model=CollectionSummary)
async def rename_collection(
    collection_id: str,
    payload: RenameCollectionRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id =current_user.get("sub") # Get user ID from the authentication token

    # Rename the collection and return the updated collection as response
    try:
        collection = await CollectionService.rename_collection(
            user_id=user_id,
            collection_id=collection_id,
            new_name=payload.newName,
        )

        # Convert raw collection item to CollectionSummary model and return as response
        return _to_summary(collection)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except CollectionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except CollectionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except CollectionServiceError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except Exception as error:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to rename collection: {error}",
        )

# Endpoint to delete an existing collection for the current user
@router.delete("/{collection_id}", response_model=DeleteCollectionResponse)
async def delete_collection(collection_id: str, current_user: dict = Depends(get_current_user)):
    user_id =current_user.get("sub") # Get user ID from the authentication token

    # Delete the collection and return the details of the deleted collection as response
    try:
        deleted = await CollectionService.delete_collection(user_id, collection_id)

        # Convert raw deleted collection info to DeleteCollectionResponse model and return as response
        return DeleteCollectionResponse(
            collectionId=str(deleted.get("collection_id") or ""),
            name=str(deleted.get("name") or ""),
            deletedFiles=int(deleted.get("deleted_files", 0) or 0),
            deletedParentChunks=int(deleted.get("deleted_parent_chunks", 0) or 0),
            deletedChildChunks=int(deleted.get("deleted_child_chunks", 0) or 0),
            warnings=list(deleted.get("warnings") or []),
        )
    except ProtectedCollectionError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except CollectionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except CollectionServiceError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except Exception as error:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete collection: {error}",
        )
