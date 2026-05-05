"""
Password hashing and verification utilities using bcrypt
"""
import os
import logging
import bcrypt

# ADDED: module-level logger replacing any future print() calls
logger = logging.getLogger(__name__)

# ADDED: work factor made explicit and configurable via environment variable.
# Defaults to 12 (bcrypt's own default) — increase to 13-14 for higher security
# at the cost of slower hashing. Never set below 10 in production.
BCRYPT_ROUNDS = int(os.getenv("BCRYPT_ROUNDS", 12))


def hash_password(password: str) -> str:
    """
    Hash a password using bcrypt

    Args:
        password: Plain text password

    Returns:
        Hashed password as string
    """
    # ADDED: guard against accidentally hashing an empty password
    if not password:
        raise ValueError("Password must not be empty")

    # FIXED: rounds now explicit and configurable via BCRYPT_ROUNDS env var
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')


def verify_password(password: str, hashed_password: str) -> bool:
    """
    Verify a password against its hash

    Args:
        password: Plain text password to verify
        hashed_password: Hashed password to compare against

    Returns:
        True if password matches, False otherwise
    """
    # ADDED: guard against empty inputs before hitting bcrypt.
    # An OAuth user accidentally routed here would have no password_hash,
    # causing bcrypt to raise ValueError instead of returning False.
    if not password or not hashed_password:
        return False

    try:
        # FIXED: wrapped in try/except — malformed or non-bcrypt hashes raise
        # ValueError/InvalidHashError. We catch all exceptions and return False
        # so callers always get a bool, never a crash.
        return bcrypt.checkpw(
            password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )
    except Exception as e:
        # Log the error for debugging but never surface it to the caller —
        # returning False is the safe default for any verification failure.
        logger.warning(f"Password verification error (returning False): {e}")
        return False