"""
Input validation utilities for user authentication
"""
import re
from email_validator import validate_email, EmailNotValidError
from typing import Optional, Tuple


def validate_email_format(email: str) -> Tuple[bool, Optional[str]]:
    """
    Validate email format
    
    Args:
        email: Email address to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        # Validate and normalize email using the validate_email library
        # Validate only the format, not deliverability (Deliverability = True, this will connect the internt to check if the email domain exists)
        # If the email syntax is invalid, it will raise an EmailNotValidError
        # Else, it will return a ValidatedEmail object
        validated = validate_email(email, check_deliverability=False)
        normalized_email = validated.normalized
        return True, None, normalized_email
    except EmailNotValidError as e:
        return False, str(e)


def validate_password_strength(password: str) -> Tuple[bool, Optional[str]]:
    """
    Validate password strength
    Requirements:
    - At least 8 characters
    - Contains at least one uppercase letter
    - Contains at least one lowercase letter
    - Contains at least one digit
    - Contains at least one special character
    
    Args:
        password: Password to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if len(password) < 8:
        return False, "❌Password must be at least 8 characters long"
    
    if not re.search(r'[A-Z]', password):
        return False, "❌Password must contain at least one uppercase letter"
    
    if not re.search(r'[a-z]', password):
        return False, "❌Password must contain at least one lowercase letter"

    if not re.search(r'\d', password):
        return False, "❌Password must contain at least one digit"

    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "❌Password must contain at least one special character"

    return True, None


def sanitize_email(email: str) -> str:
    """
    Args:
        email: Email to sanitize
        
    Returns:
        Normalized email in lowercase
    Example:
        For example, input:"   NgYoongShen@Gmail.com" or "NgYooNGSheN@gmaIL.COM     " => output: "ngyoongshen@gmail.com"
        
    """
    return email.strip().lower()
