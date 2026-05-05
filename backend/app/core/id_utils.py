from uuid6 import uuid6


def generate_uuid_v6() -> str:
    """Generate a UUIDv6 string using the uuid6 package."""
    return str(uuid6())
