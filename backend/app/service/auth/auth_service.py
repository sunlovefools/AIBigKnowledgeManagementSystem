"""
User authentication service
Handles user registration, login, and account management
"""
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any
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

# Database connection by using URL and private token in .env file
ASTRA_DB_URL = os.getenv("ASTRA_DB_URL")
ASTRA_DB_TOKEN = os.getenv("ASTRA_DB_TOKEN")

# ADDED: Auth0 environment variables
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE")
AUTH0_ALGORITHMS = ["RS256"]

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

print(f"Connected to database {database.info().name}\n")

### It ends here ###


# ADDED: Auth0 token verification helper
def verify_auth0_token(token: str) -> Dict[str, Any]:
    """
    Verify Auth0 JWT token and return decoded payload
    """
    try:
        jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
        jwks = requests.get(jwks_url).json()

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
        print(f"✅ AuthService initialized with table '{self.table_name}'\n")
        
    def _drop_table(self):
        """Drop the users table (for testing purposes)"""
        try:
            database.drop_table(self.table_name)
            print(f"✅ Table '{self.table_name}' dropped successfully")
        except Exception as e:
            print(f"❌ Failed to drop table: {e}")
            raise


    def get_table(self):
        """Get or create the users table"""
        table = database.get_collection(self.table_name)
        return table
    
    def _get_next_user_id(self) -> int:
        """Get the next available user ID"""
        try:
            result = self.table.find({})
            print(f"\n\n{result}\n\n")

            max_id = 0

            for row in result:
                if row.get('id', 0) > max_id:
                    max_id = row['id']
            return max_id + 1
        except Exception:
            return 1
    
    def email_exists(self, email: str) -> bool:
        """
        Check if an email already exists in the database
        """
        email = sanitize_email(email)
        try:
            result = self.table.find({"email": email})
            return len(list(result)) > 0
        except Exception:
            return False
    
    def register_user(self, email: str, password: str, role: str) -> Dict[str, Any]:
        """
        Register a new user
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
            
            new_user_id = inserted_result.inserted_id

            return {
                "id": new_user_id,
                "email": email,
                "user_role": role,
                "created_at": user_data["created_at"],
                "is_active": True
            }
        except Exception as e:
            raise AuthenticationError(f"Failed to register user: {e}")
    
    def login_user(self, email: str, password: str) -> Dict[str, Any]:
        """
        Authenticate a user login
        """

        email = sanitize_email(email)
        
        try:
            result = self.table.find({"email": email})
            users = list(result)

            if not users:
                raise AuthenticationError("Invalid email or password")
            
            user = users[0]
            
            if not user.get('is_active', False):
                raise AuthenticationError("Account is deactivated")
            
            if not verify_password(password, user['password_hash']):
                raise AuthenticationError("Invalid email or password")
            
            print(f"✅ User logged in successfully: {email}")
            
            return {
                "id": user['id'],
                "email": user['email'],
                "created_at": user['created_at'],
                "is_active": user['is_active']
            }
        except AuthenticationError:
            raise
        except Exception as e:
            raise AuthenticationError(f"Login failed: {e}")
    
    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """
        Get user information by email
        """
        email = sanitize_email(email)
        try:
            result = self.table.find({"email": email})
            users = list(result)
            if users:
                user = users[0]
                return {
                    "id": user['id'],
                    "email": user['email'],
                    "created_at": user.get('created_at'),
                    "is_active": user.get('is_active', True)
                }
            return None
        except Exception:
            return None

    def oauth_login(self, email: str, oauth_sub: str) -> Dict[str, Any]:
        """
        OAuth login or automatic registration

        If the OAuth user already exists, log them in.
        If they do not exist, create a new account automatically.
        """

        email = sanitize_email(email)

        try:
            result = self.table.find({"oauth_sub": oauth_sub})
            users = list(result)

            if users:
                user = users[0]

                if not user.get('is_active', True):
                    raise AuthenticationError("Account is deactivated")

                return {
                    "id": user.get('id', user.get('_id')),
                    "email": user.get('email'),
                    "user_role": user.get('user_role', "user"),
                    "created_at": user.get('created_at'),
                    "is_active": user.get('is_active', True)
                }

            user_data = {
                "email": email,
                "user_role": "user",
                "auth_provider": "oauth",
                "oauth_sub": oauth_sub,
                "created_at": datetime.now(timezone.utc),
                "is_active": True
            }

            inserted_result = self.table.insert_one(user_data)

            return {
                "id": inserted_result.inserted_id,
                "email": email,
                "user_role": "user",
                "created_at": user_data["created_at"],
                "is_active": True
            }

        except AuthenticationError:
            raise
        except Exception as e:
            raise AuthenticationError(f"OAuth login failed: {e}")


    # ADDED: Auth0 login method
    def auth0_login(self, token: str) -> Dict[str, Any]:
        """
        Login using Auth0 JWT token
        """

        try:
            payload = verify_auth0_token(token)

            email = payload.get("email")
            oauth_sub = payload.get("sub")

            if not email or not oauth_sub:
                raise AuthenticationError("Invalid Auth0 payload")

            # Reuse existing OAuth login logic
            return self.oauth_login(email, oauth_sub)

        except AuthenticationError:
            raise
        except Exception as e:
            raise AuthenticationError(f"Auth0 login failed: {e}")