# file: backend/activation/activationEntry.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.core.errors import UsageError


@dataclass(frozen=True)
class PythonActivationEntry:
    """
    Manual Python activation entry selected by platform code.

    sourcePath is a declared executable file path. It is not imported through
    normal Python package import semantics.
    """

    entryId: str
    ownerId: str
    sourcePath: Path
    callableName: str


def requireExactNonBlackString(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise UsageError(f"{name} must be a string, not {type(value).__name__}.")

    if value == "":
        raise UsageError(f"{name} must not be an empty string.")

    if value != value.strip():
        raise UsageError(f"{name} must not contain leading or trailing whitespace.")

    if value.strip() == "":
        raise UsageError(f"{name} must not be a string containing only whitespace.")

    return value


def createPythonActivationEntry(
    *,
    entryId: str,
    ownerId: str,
    sourcePath: Path,
    callableName: str,
) -> PythonActivationEntry:
    entryId = requireExactNonBlackString(entryId, "entryId")
    ownerId = requireExactNonBlackString(ownerId, "ownerId")
    callableName = requireExactNonBlackString(callableName, "callableName")

    if not isinstance(sourcePath, Path):
        raise UsageError(f"sourcePath must be a pathlib.Path, not {type(sourcePath).__name__}.")

    return PythonActivationEntry(
        entryId=entryId,
        ownerId=ownerId,
        sourcePath=sourcePath,
        callableName=callableName,
    )
