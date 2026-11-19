# file: backend/core/runtimeIds.py ; version: 1
from __future__ import annotations

import uuid

__all__ = ["newRuntimeId"]


def newRuntimeId() -> str:
    """Returns one canonical UUIDv7 string for an untyped runtime identity."""
    return str(uuid.uuid7())
