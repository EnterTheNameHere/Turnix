# file: backend/activation/activationMaterializer.py
from __future__ import annotations

from pathlib import Path, PurePosixPath

from backend.activation.activationAdapterKind import ActivationAdapterKind
from backend.activation.activationEntry import PythonActivationEntry, createPythonActivationEntry
from backend.activation.activationSpec import ActivationSpec, validateActivationSource
from backend.core.errors import UsageError


def materializeActivationEntry(
    *,
    spec: ActivationSpec,
    basePath: Path,
) -> PythonActivationEntry:
    """
    Convert a manifest-shaped activation spec into a runtime activation entry.

    This is not manifest parsing, Pack discovery, dependency solving, permission
    enforcement, sandboxing, or lifecycle state.
    """
    if not isinstance(spec, ActivationSpec):
        raise UsageError(f"spec must be an ActivationSpec, got {type(spec).__name__}.")

    sourcePath = resolveSpecSourcePath(
        basePath=basePath,
        source=spec.source,
    )

    if spec.adapterKind == ActivationAdapterKind.PYTHON_IN_PROCESS:
        return createPythonActivationEntry(
            entryId=spec.entryId,
            ownerId=spec.ownerId,
            sourcePath=sourcePath,
            callableName=spec.callableName,
        )

    raise UsageError(f"Unsupported activation adapter kind: {spec.adapterKind}.")


def resolveSpecSourcePath(
    *,
    basePath: Path,
    source: PurePosixPath,
) -> Path:
    if not isinstance(basePath, Path):
        raise UsageError(f"basePath must be a pathlib.Path, got {type(basePath).__name__}.")

    cleanSource = validateActivationSource(source)

    return basePath.joinpath(*cleanSource.parts)
