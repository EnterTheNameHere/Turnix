# backend/core/ids.py
from __future__ import annotations

import uuid
import uuid6
import secrets

__all__ = ["uuidv7", "uuidv4", "uuid_10", "uuid_12", "shortToken"]



def uuidv7(*, prefix: str = "") -> str:
    """Returns a UUIDv7 string (time-ordered), optionally prefixed."""
    return prefix + str(uuid6.uuid7())



def uuidv4(*, prefix: str = "") -> str:
    """Returns a pure random UUIDv4 string, optionally prefixed."""
    return prefix + str(uuid.uuid4())



def uuid_10(prefix = "") -> str:
    """Returns a short ID with 10 chars from a UUIDv4, optionally prefixed."""
    if not isinstance(prefix, str):
        raise TypeError("prefix must be a str")
    return f"{prefix}{uuid.uuid4().hex[:10]}"



def uuid_12(prefix = "") -> str:
    """Returns a short ID with 12 chars from a UUIDv4, optionally prefixed."""
    if not isinstance(prefix, str):
        raise TypeError("prefix must be a str")
    return f"{prefix}{uuid.uuid4().hex[:12]}"



def shortToken(nbytes: int = 12) -> str:
    """Returns compact opaque token for URLs/cookies."""
    return secrets.token_urlsafe(nbytes)
