# file: backend/core/ids.py
from __future__ import annotations

import secrets
import uuid

import uuid6

__all__: list[str] = [
    "shortToken",
    "uuidv4",
    "uuidv4hex10",
    "uuidv4hex12",
    "uuidv7",
]


def uuidv7(*, prefix: str = "") -> str:
    """Returns a UUIDv7 string, time-ordered, optionally with a prefix."""
    validatePrefix(prefix)
    return prefix + str(uuid6.uuid7())


def uuidv4(*, prefix: str = "") -> str:
    """Returns a UUIDv4 string, optionally with a prefix."""
    validatePrefix(prefix)
    return prefix + str(uuid.uuid4())


def uuidv4hex10(*, prefix: str = "") -> str:
    """Returns 10 hex characters from a UUIDv4, optionally with a prefix."""
    validatePrefix(prefix)
    return prefix + str(uuid.uuid4()).replace("-", "")[:10]


def uuidv4hex12(*, prefix: str = "") -> str:
    """Returns 12 hex characters from a UUIDv4, optionally with a prefix."""
    validatePrefix(prefix)
    return prefix + str(uuid.uuid4()).replace("-", "")[:12]


def uuidv4hex(*, prefix: str = "") -> str:
    """Returns a hex string from a UUIDv4, optionally with a prefix."""
    validatePrefix(prefix)
    return prefix + str(uuid.uuid4().hex)


def shortToken(nbytes: int = 12) -> str:
    """Returns a compact opaque token for URLs/cookies."""
    if not isinstance(nbytes, int):
        raise TypeError("nbytes must be an int")
    if nbytes < 1:
        raise ValueError("nbytes must be greater than 0")
    return secrets.token_urlsafe(nbytes)


def validatePrefix(prefix: str) -> None:
    if not isinstance(prefix, str):
        raise TypeError("prefix must be a str")
