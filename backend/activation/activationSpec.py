# file: backend/activation/activationSpec.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from backend.activation.activationAdapterKind import ActivationAdapterKind
from backend.core.errors import UsageError
from backend.core.validation import requireExactNonBlankString, typeName


@dataclass(frozen=True)
class ActivationSpec:
    """
    Manifest-shaped activation entry input.

    This is an in-memory data shape only. It is not manifest parsing, Pack
    discovery, dependency solving, permission enforcement, or lifecycle state.
    """

    entryId: str
    ownerId: str
    adapterKind: ActivationAdapterKind
    source: PurePosixPath
    callableName: str


def createActivationSpec(
    *,
    entryId: str,
    ownerId: str,
    adapterKind: ActivationAdapterKind,
    source: PurePosixPath,
    callableName: str,
) -> ActivationSpec:
    cleanEntryId = requireExactNonBlankString(entryId, "entryId")
    cleanOwnerId = requireExactNonBlankString(ownerId, "ownerId")
    cleanCallableName = requireExactNonBlankString(callableName, "callableName")

    if not isinstance(adapterKind, ActivationAdapterKind):
        raise UsageError(f"adapterKind must be an ActivationAdapterKind, not {typeName(adapterKind)}.")

    cleanSource = validateActivationSource(source)

    return ActivationSpec(
        entryId=cleanEntryId,
        ownerId=cleanOwnerId,
        adapterKind=adapterKind,
        source=cleanSource,
        callableName=cleanCallableName,
    )


def validateActivationSource(source: PurePosixPath) -> PurePosixPath:
    if not isinstance(source, PurePosixPath):
        raise UsageError(f"source must be a pathlib.PurePosixPath, not {typeName(source)}.")

    sourceText = source.as_posix()
    if sourceText == ".":
        raise UsageError("source must not be empty.")

    if source.is_absolute():
        raise UsageError(f"source must be relative: {sourceText}")

    if ".." in source.parts:
        raise UsageError(f"source must not contain '..': {sourceText}")

    return source
