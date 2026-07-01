# file: backend/core/validation.py
from __future__ import annotations

from backend.core.errors import UsageError


def requireExactNonBlankString(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise UsageError(f"{name} must be a string, not {type(value).__name__}.")

    if value == "":
        raise UsageError(f"{name} must not be an empty string.")

    if value != value.strip():
        raise UsageError(f"{name} must not contain leading or trailing whitespace.")

    if value.strip() == "":
        raise UsageError(f"{name} must not be a string containing only whitespace.")

    return value
