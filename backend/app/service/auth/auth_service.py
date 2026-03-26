"""
User authentication service.
Auth0/OAuth-only authentication and user provisioning.
"""
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from functools import lru_cache

import requests
from dotenv import load_dotenv
from jose import jwt
from astrapy import DataAPIClient

from app.core.validation import sanitize_email

load_dotenv()
logger = logging.getLogger(__name__)

ASTRA_DB_URL = os.getenv("ASTRA_DB_URL")
ASTRA_DB_TOKEN = os.getenv("ASTRA_DB_TOKEN")

AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE")
AUTH0_ALGORITHMS = ["RS256"]

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

if not ASTRA_DB_URL or not ASTRA_DB_TOKEN:
    raise ValueError(
        "Missing required environment variables!\n"
        "Please set in backend/.env file:\n"
        "  - ASTRA_DB_URL\n"
        "  - ASTRA_DB_TOKEN"
    )

client = DataAPIClient()
database = client.get_database(ASTRA_DB_URL, token=ASTRA_DB_TOKEN)
logger.info(f"Connected to database {database.info().name}")


@lru_cache(maxsize=1)
def _get_jwks() -> dict:
    """Fetch and cache Auth0 JWKS."""
    jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    response = requests.get(jwks_url, timeout=5)
    response.raise_for_status()
    return response.json()


def create_access_token(user_id: str, email: str, role: str) -> str:
    """Issue a signed internal JWT for authenticated user sessions."""
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_auth0_token(token: str) -> Dict[str, Any]:
    """Verify an Auth0 access token and return decoded claims."""
    try:
        jwks = _get_jwks()
        unverified_header = jwt.get_unverified_header(token)

        rsa_key = {}
        for key in jwks.get("keys", []):
            #TODO: Make it into a standardised tokens for getting
            if key.get("kid") == unverified_header.get("kid"):
                rsa_key = {
                    "kty": key.get("kty"),
                    "kid": key.get("kid"),
                    "use": key.get("use"),
                    "n": key.get("n"),
                    "e": key.get("e"),
                }
                break

        if not rsa_key:
            raise Exception("Unable to find appropriate key")

        # Decode the token using the RSA key and validate claims
        return jwt.decode(
            token,
            rsa_key,
            algorithms=AUTH0_ALGORITHMS,
            audience=AUTH0_AUDIENCE,
            issuer=f"https://{AUTH0_DOMAIN}/",
        )
    except Exception as e:
        raise Exception(f"Invalid Auth0 token: {e}")


def get_auth0_userinfo(token: str) -> Dict[str, Any]:
    """Fetch profile claims from Auth0 /userinfo."""
    if not AUTH0_DOMAIN:
        raise Exception("AUTH0_DOMAIN is not configured")

    userinfo_url = f"https://{AUTH0_DOMAIN}/userinfo"
    response = requests.get(
        userinfo_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


def normalize_created_at(value: Any) -> datetime:
    """Normalize Astra-created timestamp values to Python datetime."""
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

    return datetime.now(timezone.utc)


class AuthenticationError(Exception):
    """Custom auth-domain exception."""


class AuthService:
    """Auth0-backed user authentication service."""

    def __init__(self, table_name: str = "users"):
        self.table_name = table_name
        self.table = self.get_table()
        logger.info(f"AuthService initialized with table '{self.table_name}'")

    def _drop_table(self):
        """Drop users collection (test-only)."""
        try:
            database.drop_table(self.table_name)
            logger.info(f"Table '{self.table_name}' dropped successfully")
        except Exception as e:
            logger.error(f"Failed to drop table: {e}")
            raise

    def get_table(self):
        """Get or create users collection."""
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

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Return basic user profile for a sanitized email."""
        email = sanitize_email(email)
        try:
            user = self.table.find_one({"email": email})
            if user:
                return {
                    "id": str(user.get("_id")),
                    "email": user.get("email", ""),
                    "created_at": normalize_created_at(user.get("created_at")),
                    "is_active": user.get("is_active", True),
                }
            return None
        except Exception:
            return None

    def oauth_login(self, email: str, oauth_sub: str) -> Dict[str, Any]:
        """
        Auth0/OAuth login or auto-provisioning.
        Existing users are matched by oauth_sub; new users are created as oauth users.
        """
        email = sanitize_email(email)

        try:
            user = self.table.find_one({"oauth_sub": oauth_sub})
            if user:
                if not user.get("is_active", True):
                    raise AuthenticationError("Account is deactivated")

                user_id = str(user.get("_id"))
                role = user.get("user_role", "user")
                access_token = create_access_token(
                    user_id=user_id,
                    email=str(user.get("email") or email),
                    role=role,
                )
                return {
                    "id": user_id,
                    "email": str(user.get("email") or email),
                    "user_role": role,
                    "created_at": normalize_created_at(user.get("created_at")),
                    "is_active": user.get("is_active", True),
                    "access_token": access_token,
                    "token_type": "bearer",
                }

            # During cutover, fail safely if legacy local account remains.
            existing_local = self.table.find_one({"email": email, "auth_provider": "local"})
            if existing_local:
                raise AuthenticationError(
                    "A legacy local account with this email exists. Run the Auth0 migration script to remove local users."
                )

            user_data = {
                "email": email,
                "user_role": "user",
                "auth_provider": "oauth",
                "oauth_sub": oauth_sub,
                "created_at": datetime.now(timezone.utc),
                "is_active": True,
            }

            inserted_result = self.table.insert_one(user_data)
            new_user_id = str(inserted_result.inserted_id)
            access_token = create_access_token(
                user_id=new_user_id,
                email=email,
                role="user",
            )

            return {
                "id": new_user_id,
                "email": email,
                "user_role": "user",
                "created_at": normalize_created_at(user_data.get("created_at")),
                "is_active": True,
                "access_token": access_token,
                "token_type": "bearer",
            }
        except AuthenticationError:
            raise
        except Exception as e:
            raise AuthenticationError(f"OAuth login failed: {e}")

    def auth0_login(self, token: str) -> Dict[str, Any]:
        """
        Login using Auth0 access token.
        Verifies token, resolves claims, and delegates to oauth_login.
        """
        try:
            payload = verify_auth0_token(token)
            oauth_sub = payload.get("sub") # Get the subject, the subject is a unique identifier for the user in Auth0
            email = payload.get("email")

            if not oauth_sub:
                raise AuthenticationError("Invalid Auth0 payload")

            # TODO: Shuold have a standardised way to get email from the token
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

            return self.oauth_login(str(email), str(oauth_sub))
        except AuthenticationError:
            raise
        except Exception as e:
            raise AuthenticationError(f"Auth0 login failed: {e}")
