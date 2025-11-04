"""
User authentication service
Handles user registration, login, and account management
"""
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from astrapy import DataAPIClient

from auth_schema import get_users_table_definition
from password_utils import hash_password, verify_password
from validation import validate_email_format, validate_password_strength, sanitize_email

# Load environment variables
load_dotenv()

# Database connection
ASTRA_DB_URL = os.getenv("ASTRA_DB_URL")
ASTRA_DB_TOKEN = os.getenv("ASTRA_DB_TOKEN")

if not ASTRA_DB_URL or not ASTRA_DB_TOKEN:
    raise ValueError(
        "Missing required environment variables!\n"
        "Please set in backend/.env file:\n"
        "  - ASTRA_DB_URL\n"
        "  - ASTRA_DB_TOKEN"
    )

client = DataAPIClient()
database = client.get_database(ASTRA_DB_URL, token=ASTRA_DB_TOKEN)


class AuthenticationError(Exception):
    """Custom exception for authentication errors"""
    pass


class AuthService:
    """Service for handling user authentication"""
    
    def __init__(self, table_name: str = "users"):
        self.table_name = table_name
        self.table = self._get_or_create_table()
    
    def _get_or_create_table(self):
        """Get or create the users table"""
        try:
            table_definition = get_users_table_definition()
            table = database.create_table(self.table_name, definition=table_definition)
            print(f"✅ Table '{self.table_name}' created successfully")
        except Exception as e:
            error_msg = str(e).lower()
            if "already exists" in error_msg or "cannot_add_existing_table" in error_msg:
                table = database.get_table(self.table_name)
                print(f"ℹ️  Table '{self.table_name}' already exists, using existing table")
            else:
                print(f"❌ Failed to create table: {e}")
                raise
        return table
    
    def _get_next_user_id(self) -> int:
        """Get the next available user ID"""
        try:
            # Find the maximum ID in the table
            # Note: This is a simple implementation. For production, consider using UUID or database sequences
            result = self.table.find({})
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
        
        Args:
            email: Email to check
            
        Returns:
            True if email exists, False otherwise
        """
        email = sanitize_email(email)
        try:
            result = self.table.find({"email": email})
            return len(list(result)) > 0
        except Exception:
            return False
    
    def register_user(self, email: str, password: str) -> Dict[str, Any]:
        """
        Register a new user
        
        Args:
            email: User's email
            password: User's plain text password
            
        Returns:
            Dictionary with user information (without password)
            
        Raises:
            AuthenticationError: If validation fails or email already exists
        """
        # Sanitize email
        email = sanitize_email(email)
        
        # Validate email format
        is_valid_email, email_error = validate_email_format(email)
        if not is_valid_email:
            raise AuthenticationError(f"Invalid email format: {email_error}")
        
        # Validate password strength
        is_valid_password, password_error = validate_password_strength(password)
        if not is_valid_password:
            raise AuthenticationError(f"Weak password: {password_error}")
        
        # Check for duplicate email
        if self.email_exists(email):
            raise AuthenticationError(f"Account with email '{email}' already exists")
        
        # Hash password
        password_hash = hash_password(password)
        
        # Create user record
        user_id = self._get_next_user_id()
        user_data = {
            "id": user_id,
            "email": email,
            "password_hash": password_hash,
            "created_at": datetime.now(timezone.utc),
            "is_active": True
        }
        
        # Insert into database
        try:
            self.table.insert_one(user_data)
            print(f"✅ User registered successfully: {email}")
            
            # Return user data without password
            return {
                "id": user_id,
                "email": email,
                "created_at": user_data["created_at"],
                "is_active": True
            }
        except Exception as e:
            raise AuthenticationError(f"Failed to register user: {e}")
    
    def login_user(self, email: str, password: str) -> Dict[str, Any]:
        """
        Authenticate a user login
        
        Args:
            email: User's email
            password: User's plain text password
            
        Returns:
            Dictionary with user information if login successful
            
        Raises:
            AuthenticationError: If credentials are invalid
        """
        # Sanitize email
        email = sanitize_email(email)
        
        # Find user by email
        try:
            result = self.table.find({"email": email})
            users = list(result)
            
            if not users:
                raise AuthenticationError("Invalid email or password")
            
            user = users[0]
            
            # Check if account is active
            if not user.get('is_active', False):
                raise AuthenticationError("Account is deactivated")
            
            # Verify password
            if not verify_password(password, user['password_hash']):
                raise AuthenticationError("Invalid email or password")
            
            print(f"✅ User logged in successfully: {email}")
            
            # Return user data without password
            return {
                "id": user['id'],
                "email": user['email'],
                "created_at": user.get('created_at'),
                "is_active": user.get('is_active', True)
            }
        except AuthenticationError:
            raise
        except Exception as e:
            raise AuthenticationError(f"Login failed: {e}")
    
    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """
        Get user information by email
        
        Args:
            email: User's email
            
        Returns:
            User information dictionary or None if not found
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
