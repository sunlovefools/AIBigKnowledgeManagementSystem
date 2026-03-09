import logging
from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

# Import the service and its custom error
from app.service.auth.auth_service import AuthService, AuthenticationError

# ADDED: module-level logger replacing print() calls
logger = logging.getLogger(__name__)

# --- Pydantic Models (Data Schemas) ---
# These models define the exact shape of the data you expect.
# FastAPI will use these to validate the incoming request JSON.

class UserCreateRequest(BaseModel):
    """
    Data required to register a new user.
    This matches the { email, password, role } object from your frontend.
    """
    # These are the fields expected in the request body when registering a user.
    email: str
    password: str
    role: str

class UserLoginRequest(BaseModel):
    """Data required to log in a user."""
    # These are the fields expected in the request body when logging in.
    email: str
    password: str

class UserDisplayResponse(BaseModel):
    """
    Data sent back to the client after a successful
    registration or login. This model ensures the
    'password_hash' is NEVER sent back.
    """
    # These are the fields that will be returned in the response.
    id: str                         # UUID will be given from the database
    email: str
    user_role: str                  # FIXED: was missing — needed for frontend role-based UI
    created_at: datetime
    is_active: bool
    access_token: str               # ADDED: JWT issued by auth_service — must be returned to client
    token_type: str                 # ADDED: always "bearer" — tells client how to use the token

    class Config:
        from_attributes = True      # Allows FastAPI to convert your database/dict object to this model


# ADDED: request model used when logging in with Auth0 JWT token
class Auth0LoginRequest(BaseModel):
    token: str


# --- Dependency Injection ---

# FIXED: was a module-level singleton — now uses FastAPI Depends() for proper
# lifecycle management and testability (tests can override this dependency).
def get_auth_service() -> AuthService:
    """
    FastAPI dependency that provides an AuthService instance.
    Raises 503 if the service fails to initialize (e.g. missing env vars).
    """
    try:
        return AuthService()
    except ValueError as e:
        logger.critical(f"Failed to initialize AuthService: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is not available."
        )


# Setup the API router

# Create a router for authentication endpoints
router = APIRouter()


# --- API Endpoints ---

# Simple health check endpoint for this module
@router.get("/health")
def auth_health():
    return {"authentication": "ok"}


# Endpoint to register a new user
@router.post(
    "/register",
    response_model=UserDisplayResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Authentication"]
)
async def register_user(
    user_data: UserCreateRequest,
    auth_service: AuthService = Depends(get_auth_service)  # FIXED: injected via Depends
):
    """
    Handle new user registration.
    Receives email, password, and role from the frontend.
    Returns user info and a signed JWT access token.
    """
    logger.info("Register user called")  # FIXED: was print()

    try:
        new_user = auth_service.register_user(
            email=user_data.email,
            password=user_data.password,
            role=user_data.role
        )

        return new_user

    except AuthenticationError as e:
        if "already exists" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e)
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )


@router.post(
    "/login",
    response_model=UserDisplayResponse,
    tags=["Authentication"]
)
async def login_user(
    user_data: UserLoginRequest,
    auth_service: AuthService = Depends(get_auth_service)  # FIXED: injected via Depends
):
    """
    Handle user login.
    Receives email and password.
    Returns user info and a signed JWT access token.
    """
    try:
        # Call existing login logic
        user = auth_service.login_user(
            email=user_data.email,
            password=user_data.password
        )
        return user

    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},  # Standard for login errors
        )


# Added: endpoint that allows login using an Auth0 JWT token
@router.post(
    "/auth0-login",
    response_model=UserDisplayResponse,
    tags=["Authentication"]
)
async def auth0_login(
    auth_data: Auth0LoginRequest,
    auth_service: AuthService = Depends(get_auth_service)  # FIXED: injected via Depends
):
    """
    Login using Auth0 JWT token.
    Frontend sends the token received from Auth0.
    Returns user info and a signed JWT access token.
    """
    try:
        # Calls the AuthService method that verifies the Auth0 token
        user = auth_service.auth0_login(
            token=auth_data.token
        )

        return user

    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )