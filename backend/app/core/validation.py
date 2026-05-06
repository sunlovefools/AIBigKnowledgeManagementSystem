"""
Input validation utilities for user authentication
"""
import re
import logging
from email_validator import validate_email, EmailNotValidError
from typing import Optional, Tuple

# ADDED: module-level logger
logger = logging.getLogger(__name__)


def validate_email_format(email: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validate email format

    Args:
        email: Email address to validate

    Returns:
        Tuple of (is_valid, error_message, normalized_email)
        - On success: (True,  None,          "normalized@email.com")
        - On failure: (False, "error reason", None)

    FIXED: return type was Tuple[bool, Optional[str]] — missing the third
    element (normalized_email). The error path was only returning 2 values,
    which caused a ValueError unpack crash in auth_service.py on any invalid email.
    """
    try:
        # Validate and normalize email using the validate_email library
        # Validate only the format, not deliverability (Deliverability = True, this will connect the internet to check if the email domain exists)
        # If the email syntax is invalid, it will raise an EmailNotValidError
        # Else, it will return a ValidatedEmail object
        validated = validate_email(email, check_deliverability=False)
        normalized_email = validated.normalized
        return True, None, normalized_email
    except EmailNotValidError as e:
        # FIXED: was returning (False, str(e)) — only 2 values.
        # Callers unpack 3 values so this raised ValueError before the error
        # message could even be used. Now returns None as the third element.
        return False, str(e), None


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
    # ADDED: guard against None being passed — len(None) raises TypeError
    if not password or not isinstance(password, str):
        return False, "❌Password must not be empty"

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
    Basic sanitization: strip whitespace and lowercase the email.

    NOTE: This is a lightweight fallback for cases where full validation
    is not needed (e.g. lookups). For registration and login, always call
    validate_email_format() instead — it uses the email_validator library
    which handles unicode, subaddresses, and proper normalisation.
    sanitize_email() alone is weaker and should not be used as a substitute.

    Args:
        email: Email to sanitize

    Returns:
        Normalized email in lowercase

    Example:
        Input:  "   NgYoongShen@Gmail.com" or "NgYooNGSheN@gmaIL.COM     "
        Output: "ngyoongshen@gmail.com"
    """
    # ADDED: guard against None being passed
    if not email or not isinstance(email, str):
        return ""

    return email.strip().lower()