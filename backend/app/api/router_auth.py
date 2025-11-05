from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from datetime import datetime

# Import the service and its custom error
from app.service.auth_service import AuthService, AuthenticationError

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
    id: str # UUID will be given from the database
    email: str
    created_at: datetime
    is_active: bool

    class Config:
        from_attributes = True  # Allows FastAPI to convert your database/dict object to this model

# Setup the API router and service instance

# Create a router for authentication endpoints
router = APIRouter()

# Initialize your authentication service
# This one instance will be used by all requests.
try:
    auth_service = AuthService()
except ValueError as e:
    # This catches the ASTRA_DB_URL missing error
    print(f"❌ CRITICAL ERROR: Failed to initialize AuthService. {e}")
    auth_service = None

# --- API Endpoints ---

# Endpoint to register a new user
# Explain what are all these parameters mean
# - "/register": The URL path for this endpoint
# - response_model=UserDisplayResponse: The Pydantic model that defines the shape of the response, 
# All of these is to send back to the client after successful registration
# - status_code=status.HTTP_201_CREATED: The HTTP status code to return on success
@router.post(
    "/register",
    response_model=UserDisplayResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Authentication"]
)
# When a POST request is made to /register, this function is called
# The first thing it does is to parse the incoming JSON body into a UserCreateRequest object as defined above
# The incoming JSON body is:
# {
#     "email": "user@example.com",
#     "password": "securepassword",
#     "role": "user"
# }
# FastAPI automatically does this parsing and validation for you
# If the JSON body does not match the UserCreateRequest model, FastAPI will return a 422 Unprocessable Entity error automatically
async def register_user(user_data: UserCreateRequest):
    """
    Handle new user registration.
    Receives email, password, and role from the frontend.
    """
    # Check if the auth service failed to start
    print("Register user called")
    if not auth_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is not available."
        )

    try:
        # 3. Call your service logic
        # Note: Your current service only uses email and password.
        # The 'role' from user_data is available but not passed to the service.
        # You would need to update your AuthService to handle 'role' if needed.
        new_user = auth_service.register_user(
            email=user_data.email,
            password=user_data.password,
            role=user_data.role
        )
        # FastAPI will automatically format 'new_user' using the UserDisplayResponse model
        # The body of new_user is like:
        #{
        #     "id": 1,
        #     "email": "user@example.com",
        #     "created_at": "2023-01-01T00:00:00Z",
        #     "is_active": true
        # }
        return new_user

    except AuthenticationError as e:
        # 4. Handle errors from your service
        if "already exists" in str(e):
            raise HTTPException(
                # Pass the error back to the client
                status_code=status.HTTP_409_CONFLICT,
                # The detail message will be shown to the client
                # The structure is:# {
                #     "detail": "Account with email 'user@example.com' already exists"
                # }
                detail=str(e)
            )
        else:
            # For "Invalid email" or "Weak password"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )

@router.post(
    "/login",
    response_model=UserDisplayResponse,
    tags=["Authentication"]
)
async def login_user(user_data: UserLoginRequest):
    """
    Handle user login.
    Receives email and password.
    """
    if not auth_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is not available."
        )

    try:
        # Your service's login_user returns the user dictionary,
        # so we return that here.
        user = auth_service.login_user(
            email=user_data.email,
            password=user_data.password
        )
        return user

    except AuthenticationError as e:
        # For "Invalid email or password" or "Account is deactivated"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"}, # Standard for login errors
        )