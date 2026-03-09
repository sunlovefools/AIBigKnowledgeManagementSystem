"""
FastAPI dependency functions for authentication and authorisation.

Usage:
    from app.core.dependencies import get_current_user, get_admin_user

    # Protect any route by adding one of these as a Depends() parameter:

    @router.get("/protected")
    async def protected_route(current_user: dict = Depends(get_current_user)):
        user_id = current_user["sub"]   # AstraDB document _id
        email   = current_user["email"]
        role    = current_user["role"]

    @router.delete("/admin-only")
    async def admin_route(current_user: dict = Depends(get_admin_user)):
        ...
"""
import os
import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

logger = logging.getLogger(__name__)

# Must match the values used in auth_service.py → create_access_token()
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM  = "HS256"

# HTTPBearer extracts the token from the Authorization: Bearer <token> header.
# auto_error=False lets us return a cleaner 401 instead of FastAPI's default 403.
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)
) -> dict:
    """
    Decodes and validates the JWT from the Authorization header.

    Returns the decoded token payload:
        {
            "sub":   "<user_id>",    # AstraDB _id — use this for all DB queries
            "email": "<email>",
            "role":  "user" | "admin",
            "exp":   <unix timestamp>
        }

    Raises HTTP 401 if:
        - No token is provided
        - Token is expired
        - Token signature is invalid
        - Token is missing required fields
    """
    # Catches missing or malformed Authorization header
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated — no token provided",
            headers={"WWW-Authenticate": "Bearer"}
        )

    if not JWT_SECRET_KEY:
        # Fail loudly in development; this means JWT_SECRET_KEY is missing from .env
        logger.critical("JWT_SECRET_KEY is not set — cannot verify tokens")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server authentication configuration error"
        )

    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM]
        )

        # Validate required claims are present in the token
        user_id = payload.get("sub")
        email   = payload.get("email")
        role    = payload.get("role")

        if not user_id or not email or not role:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token — missing required claims",
                headers={"WWW-Authenticate": "Bearer"}
            )

        return payload

    except JWTError as e:
        logger.warning(f"JWT verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"}
        )


def get_admin_user(
    current_user: dict = Depends(get_current_user)
) -> dict:
    """
    Extends get_current_user — also enforces that the user has the 'admin' role.

    Use this on any route that should only be accessible to admins.

    Raises HTTP 403 if the authenticated user is not an admin.
    """
    if current_user.get("role") != "admin":
        logger.warning(
            f"Non-admin user '{current_user.get('email')}' attempted to access admin route"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )

    return current_user