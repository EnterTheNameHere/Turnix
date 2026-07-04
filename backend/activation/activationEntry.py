# file: backend/activation/activationEntry.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.activation.activationAdapterKind import ActivationAdapterKind
from backend.core.errors import UsageError
from backend.core.validation import requireExactNonBlankString, typeName


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
    adapterKind: ActivationAdapterKind


def createPythonActivationEntry(
    *,
    entryId: str,
    ownerId: str,
    sourcePath: Path,
    callableName: str,
) -> PythonActivationEntry:
    cleanEntryId = requireExactNonBlankString(entryId, "entryId")
    cleanOwnerId = requireExactNonBlankString(ownerId, "ownerId")
    cleanCallableName = requireExactNonBlankString(callableName, "callableName")

    if not isinstance(sourcePath, Path):
        raise UsageError(f"sourcePath must be a pathlib.Path, not {typeName(sourcePath)}.")

    return PythonActivationEntry(
        entryId=cleanEntryId,
        ownerId=cleanOwnerId,
        sourcePath=sourcePath,
        callableName=cleanCallableName,
        adapterKind=ActivationAdapterKind.PYTHON_IN_PROCESS,
    )
