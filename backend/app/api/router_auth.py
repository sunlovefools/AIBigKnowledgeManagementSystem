import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel

from app.service.auth.auth_service import AuthService, AuthenticationError

logger = logging.getLogger(__name__)


class UserDisplayResponse(BaseModel):
    """Auth session payload returned after successful Auth0 login exchange."""

    id: str
    email: str
    user_role: str #TODO: Why is this needed?
    created_at: datetime
    is_active: bool
    access_token: str # Access_token: JWT issued by auth_service (Used by frontend for authenticated requests)
    token_type: str # Token_type: always "bearer" — tells client how to use the token

    class Config:
        from_attributes = True


class Auth0LoginRequest(BaseModel):
    token: str

# Dependency injection function to provide AuthService instance to all the endpoints in this router
def get_auth_service() -> AuthService:
    """FastAPI dependency that provides AuthService."""
    try:
        return AuthService()
    except ValueError as e:
        logger.critical(f"Failed to initialize AuthService: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is not available.",
        )


router = APIRouter()

# Simple health check endpoint for authentication service
@router.get("/health")
def auth_health():
    return {"authentication": "ok"}

# Endpoint for Auth0 login exchange. 
# Frontend sends Auth0 access token, backend verifies it and returns internal access token + user info.
@router.post(
    "/auth0-login",
    response_model=UserDisplayResponse,
    tags=["Authentication"],
)
async def auth0_login(
    auth_data: Auth0LoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    Login using Auth0 access token sent by frontend.
    Returns user info and a signed internal access token.
    """
    try:
        return auth_service.auth0_login(token=auth_data.token)
    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )
