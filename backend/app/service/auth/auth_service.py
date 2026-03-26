"""
User authentication service
Handles user registration, login, and account management
"""
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from functools import lru_cache
from dotenv import load_dotenv
from astrapy import DataAPIClient

# ADDED: Auth0 libraries
import requests
from jose import jwt

# from auth_schema import get_users_table_definition
from app.core.password_utils import hash_password, verify_password
from app.core.validation import validate_email_format, validate_password_strength, sanitize_email

### This part of the code will run once when the module is imported ###
# Load environment variables from .env file
load_dotenv()

# Set up module-level logger (replaces print statements)
logger = logging.getLogger(__name__)

# Database connection by using URL and private token in .env file
ASTRA_DB_URL = os.getenv("ASTRA_DB_URL")
ASTRA_DB_TOKEN = os.getenv("ASTRA_DB_TOKEN")

# ADDED: Auth0 environment variables
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE")
AUTH0_ALGORITHMS = ["RS256"]

# ADDED: JWT issuance config
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# If either variable is missing, raise an error
if not ASTRA_DB_URL or not ASTRA_DB_TOKEN:
    raise ValueError(
        "Missing required environment variables!\n"
        "Please set in backend/.env file:\n"
        "  - ASTRA_DB_URL\n"
        "  - ASTRA_DB_TOKEN"
    )

# Initialize database client
client = DataAPIClient()
# Connect to the database
database = client.get_database(ASTRA_DB_URL, token=ASTRA_DB_TOKEN)

logger.info(f"Connected to database {database.info().name}")

### It ends here ###


# ADDED: JWKS cached at module level with lru_cache — avoids a live HTTP call on every token verify
@lru_cache(maxsize=1)
def _get_jwks() -> dict:
    """
    Fetch and cache Auth0 JWKS (JSON Web Key Set).
    Cached after first call — avoids network call on every token verification.
    Call _get_jwks.cache_clear() to force a refresh if keys rotate.
    """
    jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    response = requests.get(jwks_url, timeout=5)
    response.raise_for_status()
    return response.json()


# ADDED: JWT issuance helper — called after successful login/oauth to issue a session token
def create_access_token(user_id: str, email: str, role: str) -> str:
    """
    Issue a signed JWT for the authenticated user.
    Token includes user_id (sub), email, role, and expiry.
    """
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


# ADDED: Auth0 token verification helper
def verify_auth0_token(token: str) -> Dict[str, Any]:
    """
    Verify Auth0 JWT token and return decoded payload.
    Uses cached JWKS — no network call after first verification.
    """
    try:
        # Use cached JWKS instead of fetching on every call
        jwks = _get_jwks()

        unverified_header = jwt.get_unverified_header(token)

        rsa_key = {}

        for key in jwks["keys"]:
            if key["kid"] == unverified_header["kid"]:
                rsa_key = {
                    "kty": key["kty"],
                    "kid": key["kid"],
                    "use": key["use"],
                    "n": key["n"],
                    "e": key["e"]
                }

        if rsa_key:
            payload = jwt.decode(
                token,
                rsa_key,
                algorithms=AUTH0_ALGORITHMS,
                audience=AUTH0_AUDIENCE,
                issuer=f"https://{AUTH0_DOMAIN}/"
            )

            return payload

        raise Exception("Unable to find appropriate key")

    except Exception as e:
        raise Exception(f"Invalid Auth0 token: {e}")


def get_auth0_userinfo(token: str) -> Dict[str, Any]:
    """
    Fetch user profile from Auth0 /userinfo endpoint.
    Useful when access token omits email claim.
    """
    if not AUTH0_DOMAIN:
        raise Exception("AUTH0_DOMAIN is not configured")

    userinfo_url = f"https://{AUTH0_DOMAIN}/userinfo"
    response = requests.get(
        userinfo_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=5
    )
    response.raise_for_status()
    return response.json()


def normalize_created_at(value: Any) -> datetime:
    """
    Normalize stored created_at values into a timezone-aware datetime.
    Handles Astra DataAPITimestamp and plain datetime/string values.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    timestamp_ms = getattr(value, "timestamp_ms", None)
    if isinstance(timestamp_ms, (int, float)):
        return datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=timezone.utc)

    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    # Last-resort fallback prevents response-model validation errors.
    return datetime.now(timezone.utc)


class AuthenticationError(Exception):
    # Custom exception for authentication errors
    # Can 'pass' pass the exception to the caller?
    # Yes, 'pass' allows the exception to be raised and handled by the caller.
    pass


# AuthService class
class AuthService:
    """Service for handling user authentication"""

    # Initialize the AuthService
    def __init__(self, table_name: str = "users"):
        # Set up the users table with the "users" table schema
        self.table_name = table_name
        self.table = self.get_table()
        logger.info(f"AuthService initialized with table '{self.table_name}'")

    def _drop_table(self):
        """Drop the users table (for testing purposes only — do not call in production)"""
        try:
            database.drop_table(self.table_name)
            logger.info(f"Table '{self.table_name}' dropped successfully")
        except Exception as e:
            logger.error(f"Failed to drop table: {e}")
            raise

    def get_table(self):
        """Get or create the users collection."""
        try:
            existing_names = set(database.list_collection_names())
            if self.table_name not in existing_names:
                logger.warning(
                    f"Collection '{self.table_name}' does not exist. Creating it now."
                )
                database.create_collection(self.table_name)

            return database.get_collection(self.table_name)
        except Exception as e:
            logger.error(f"Failed to initialize collection '{self.table_name}': {e}")
            raise

    def _get_next_user_id(self) -> int:
        """
        Get the next available user ID.
        NOTE: Kept for reference but not used — AstraDB auto-generates _id.
        Calling this on a large collection is expensive (full scan).
        """
        try:
            result = self.table.find({})
            logger.debug(f"_get_next_user_id result: {result}")

            max_id = 0

            for row in result:
                if row.get('id', 0) > max_id:
                    max_id = row['id']
            return max_id + 1
        except Exception:
            return 1

    def email_exists(self, email: str) -> bool:
        """
        Check if an email already exists in the database.
        Uses find_one instead of find for efficiency.
        """
        email = sanitize_email(email)
        try:
            # FIXED: find_one is more efficient than find + list conversion
            result = self.table.find_one({"email": email})
            return result is not None
        except Exception:
            return False

    def register_user(self, email: str, password: str, role: str) -> Dict[str, Any]:
        """
        Register a new user.
        Returns user data + a signed JWT access token.
        """

        is_valid_email, email_error, email = validate_email_format(email)
        if not is_valid_email:
            raise AuthenticationError(f"Invalid email format: {email_error}")

        is_valid_password, password_error = validate_password_strength(password)
        if not is_valid_password:
            raise AuthenticationError(f"Weak password: {password_error}")

        if self.email_exists(email):
            raise AuthenticationError(f"Account with email '{email}' already exists")

        if role not in ["user", "admin"]:
            raise AuthenticationError(f"Invalid user role: {role}")

        password_hash = hash_password(password)

        user_data = {
            "email": email,
            "user_role": role,
            "password_hash": password_hash,

            # OAuth fields added
            "auth_provider": "local",
            "oauth_sub": None,

            "created_at": datetime.now(timezone.utc),
            "is_active": True
        }

        try:
            inserted_result = self.table.insert_one(user_data)

            # FIXED: AstraDB returns _id, not id
            new_user_id = str(inserted_result.inserted_id)

            # ADDED: Issue JWT on registration so the user is immediately authenticated
            access_token = create_access_token(
                user_id=new_user_id,
                email=email,
                role=role
            )

            return {
                "id": new_user_id,
                "email": email,
                "user_role": role,
                "created_at": normalize_created_at(user_data.get("created_at")),
                "is_active": True,
                "access_token": access_token,   # ADDED
                "token_type": "bearer"           # ADDED
            }
        except Exception as e:
            raise AuthenticationError(f"Failed to register user: {e}")

    def login_user(self, email: str, password: str) -> Dict[str, Any]:
        """
        Authenticate a user login.
        Returns user data + a signed JWT access token.
        """

        email = sanitize_email(email)

        try:
            # FIXED: find_one instead of find + list conversion
            user = self.table.find_one({"email": email})

            if not user:
                raise AuthenticationError("Invalid email or password")

            if not user.get('is_active', False):
                raise AuthenticationError("Account is deactivated")

            if not verify_password(password, user['password_hash']):
                raise AuthenticationError("Invalid email or password")

            logger.info(f"User logged in successfully: {email}")

            # FIXED: AstraDB uses _id not id
            user_id = str(user.get('_id'))

            # ADDED: user_role was missing from this return — needed for JWT claims
            role = user.get('user_role', 'user')

            # ADDED: Issue JWT so caller has a session token
            access_token = create_access_token(
                user_id=user_id,
                email=user['email'],
                role=role
            )

            return {
                "id": user_id,
                "email": user['email'],
                "user_role": role,                  # FIXED: was missing
                "created_at": normalize_created_at(user.get("created_at")),
                "is_active": user['is_active'],
                "access_token": access_token,       # ADDED
                "token_type": "bearer"              # ADDED
            }
        except AuthenticationError:
            raise
        except Exception as e:
            raise AuthenticationError(f"Login failed: {e}")

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """
        Get user information by email.
        """
        email = sanitize_email(email)
        try:
            # FIXED: find_one instead of find + list conversion
            user = self.table.find_one({"email": email})
            if user:
                return {
                    # FIXED: AstraDB uses _id not id
                    "id": str(user.get('_id')),
                    "email": user['email'],
                    "created_at": normalize_created_at(user.get("created_at")),
                    "is_active": user.get('is_active', True)
                }
            return None
        except Exception:
            return None

    def oauth_login(self, email: str, oauth_sub: str) -> Dict[str, Any]:
        """
        OAuth login or automatic registration.

        If the OAuth user already exists (matched by oauth_sub), log them in.
        If a local account exists with the same email, raise an error to prevent duplicate accounts.
        If they do not exist at all, create a new account automatically.
        Returns user data + a signed JWT access token.
        """

        email = sanitize_email(email)

        try:
            # FIXED: find_one instead of find + list conversion
            user = self.table.find_one({"oauth_sub": oauth_sub})

            if user:
                if not user.get('is_active', True):
                    raise AuthenticationError("Account is deactivated")

                # FIXED: AstraDB uses _id
                user_id = str(user.get('_id'))
                role = user.get('user_role', 'user')

                # ADDED: Issue JWT for existing OAuth user
                access_token = create_access_token(
                    user_id=user_id,
                    email=user.get('email'),
                    role=role
                )

                return {
                    "id": user_id,
                    "email": user.get('email'),
                    "user_role": role,
                    "created_at": normalize_created_at(user.get("created_at")),
                    "is_active": user.get('is_active', True),
                    "access_token": access_token,   # ADDED
                    "token_type": "bearer"          # ADDED
                }

            # ADDED: Check for existing local account with same email to prevent duplicate accounts
            existing_local = self.table.find_one({"email": email, "auth_provider": "local"})
            if existing_local:
                raise AuthenticationError(
                    "An account with this email already exists. Please log in with your password."
                )

            user_data = {
                "email": email,
                "user_role": "user",
                "auth_provider": "oauth",
                "oauth_sub": oauth_sub,
                "created_at": datetime.now(timezone.utc),
                "is_active": True
            }

            inserted_result = self.table.insert_one(user_data)

            # FIXED: AstraDB uses _id
            new_user_id = str(inserted_result.inserted_id)

            # ADDED: Issue JWT for newly created OAuth user
            access_token = create_access_token(
                user_id=new_user_id,
                email=email,
                role="user"
            )

            return {
                "id": new_user_id,
                "email": email,
                "user_role": "user",
                "created_at": normalize_created_at(user_data.get("created_at")),
                "is_active": True,
                "access_token": access_token,   # ADDED
                "token_type": "bearer"          # ADDED
            }

        except AuthenticationError:
            raise
        except Exception as e:
            raise AuthenticationError(f"OAuth login failed: {e}")

    # ADDED: Auth0 login method
    def auth0_login(self, token: str) -> Dict[str, Any]:
        """
        Login using Auth0 JWT token.
        Verifies the token, extracts identity, and delegates to oauth_login.
        Returns user data + a signed JWT access token.
        """

        try:
            payload = verify_auth0_token(token)

            oauth_sub = payload.get("sub")
            email = payload.get("email")

            if not oauth_sub:
                raise AuthenticationError("Invalid Auth0 payload")

            # Access tokens for custom APIs may not include email by default.
            # Fallback to /userinfo so we can still map/create the local user.
            if not email:
                try:
                    userinfo = get_auth0_userinfo(token)
                    userinfo_sub = userinfo.get("sub")
                    userinfo_email = userinfo.get("email")

                    if userinfo_sub and userinfo_sub != oauth_sub:
                        raise AuthenticationError("Invalid Auth0 payload: subject mismatch")

                    email = userinfo_email
                except AuthenticationError:
                    raise
                except Exception as e:
                    raise AuthenticationError(f"Invalid Auth0 payload: {e}")

            if not email:
                raise AuthenticationError(
                    "Invalid Auth0 payload: missing email claim. "
                    "Request 'email' scope or add email claim in Auth0."
                )

            # Reuse existing OAuth login logic
            return self.oauth_login(email, oauth_sub)

        except AuthenticationError:
            raise
        except Exception as e:
            raise AuthenticationError(f"Auth0 login failed: {e}")
