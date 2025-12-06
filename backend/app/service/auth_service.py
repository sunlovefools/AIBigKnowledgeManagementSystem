"""
User authentication service
Handles user registration, login, and account management
"""
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from astrapy import DataAPIClient

# from auth_schema import get_users_table_definition
from app.core.password_utils import hash_password, verify_password
from app.core.validation import validate_email_format, validate_password_strength, sanitize_email

### This part of the code will run once when the module is imported ###
# Load environment variables from .env file
load_dotenv()

# Database connection by using URL and private token in .env file
ASTRA_DB_URL = os.getenv("ASTRA_DB_URL")
ASTRA_DB_TOKEN = os.getenv("ASTRA_DB_TOKEN")

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
            # Find the maximum ID in the table
            # Note: This is a simple implementation. For production, consider using UUID or database sequences
            result = self.table.find({})
            # .find() will return a cursor object that is iterable (Imagine it's a pointer point to the dataset over at the database [cloud])
            print(f"\n\n{result}\n\n")
            # Get a max_id to keep track of the latest id from the table
            max_id = 0

            # Loop through each row in the result to find the maximum id
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
        # Sanitize email
        email = sanitize_email(email)
        try:
            # Check for existing email
            result = self.table.find({"email": email})
            return len(list(result)) > 0
        except Exception:
            return False
    
    def register_user(self, email: str, password: str, role: str) -> Dict[str, Any]:
        """
        Register a new user
        
        Args:
            email: User's email
            password: User's plain text password
            
        Returns:
            Dictionary with user information (without password)
            {
                "id": int,
                "email": str,
                "user_role": str,
                "created_at": datetime,
                "is_active": bool
            }

        Raises:
            AuthenticationError: If validation fails or email already exists
        """
        # Sanitize email into lowercase and remove trailing spaces or leading spaces
        # email = sanitize_email(email)
        
        # Validate email format and return 
        # is_valid_email: Boolean indicating if email is valid
        # email_error: Error message if invalid
        # email: Normalized email (Normalised email is in lowercase and trimmed [Same as sanitize_email function, that is why I commented out the sanitize_email line above])
        is_valid_email, email_error, email = validate_email_format(email)
        # If email is invalid, raise error
        if not is_valid_email:
            raise AuthenticationError(f"Invalid email format: {email_error}")
        
        # Validate password strength, if the password is weak, raise error
        is_valid_password, password_error = validate_password_strength(password)
        if not is_valid_password:
            raise AuthenticationError(f"Weak password: {password_error}")
        
        # Check for duplicate email, if exists, raise error
        if self.email_exists(email):
            raise AuthenticationError(f"Account with email '{email}' already exists")

        # Validate user role
        if role not in ["user", "admin"]:
            raise AuthenticationError(f"Invalid user role: {role}")

        # Hash password using bcrypt
        password_hash = hash_password(password)

        # Prepare user data dictionary
        user_data = {
            "email": email,
            "user_role": role,
            "password_hash": password_hash,
            "created_at": datetime.now(timezone.utc),
            "is_active": True
        }
        
        # Insert into database
        try:
            # Inset the data into the table
            inserted_result = self.table.insert_one(user_data)
            
            # Get the new user ID (_id) assigned by the database
            new_user_id = inserted_result.inserted_id
            # Return user data without password
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
            # Try to find the user in the database
            # find() method will return a cursor object that is iterable, can you list() it to get all the results
            result = self.table.find({"email": email})
            users = list(result)

            # Example of output: 
            # [{'id': 1, 
            # 'created_at': DataAPITimestamp(timestamp_ms=1762275590557 [2025-11-04T16:59:50.557Z]),
            #  'email': 'test@example.com',
            #  'is_active': True,
            #  'password_hash': '$2b$12$zpycz1q9DASaDwt9IGULYehixv6JiFrdJ/O7pGndmvrH2ZhwGNCXC'}]

            # Example of accessing the data of email: 
            # users[0]['email'] => 'test@example.com'
            
            # If no user found with that email, raise error
            if not users:
                raise AuthenticationError("Invalid email or password")
            
            # Get the first user (there should only be one due to unique email constraint)
            user = users[0]
            
            # Check if account is active
            # If account is deactivated, raise error
            if not user.get('is_active', False):
                raise AuthenticationError("Account is deactivated")
            
            # Verify password using hashed password
            if not verify_password(password, user['password_hash']):
                raise AuthenticationError("Invalid email or password")
            
            print(f"✅ User logged in successfully: {email}")
            
            # Return user data without password
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
