# file: backend/activation/activationDeclaration.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.activation.activationMaterializer import materializeActivationEntry
from backend.activation.activationPlan import ActivationPlan, createActivationPlan
from backend.activation.activationSpec import ActivationSpec
from backend.core.errors import UsageError
from backend.core.validation import requireExactNonBlankString


@dataclass(frozen=True)
class ActivationDeclaration:
    """
    In-memory manifest-shaped activation group.

    This is not manifest file parsing, Pack discovery, dependency solving,
    version resolution, permission enforcement, or activation lifecycle state.
    """

    planId: str
    entries: tuple[ActivationSpec, ...]


def createActivationDeclaration(
    *,
    planId: str,
    entries: tuple[ActivationSpec, ...],
) -> ActivationDeclaration:
    cleanPlanId = requireExactNonBlankString(planId, "planId")

    if not isinstance(entries, tuple):
        raise UsageError(f"entries must be a tuple, not {type(entries).__name__}.")

    if not entries:
        raise UsageError("entries must be non-empty.")

    for index, entry in enumerate(entries):
        if not isinstance(entry, ActivationSpec):
            raise UsageError(f"entries[{index}] must be an ActivationSpec, not {type(entry).__name__}.")

    return ActivationDeclaration(
        planId=cleanPlanId,
        entries=tuple(entries),
    )


def materializeActivationPlan(
    *,
    manifest: ActivationDeclaration,
    basePath: Path,
) -> ActivationPlan:
    if not isinstance(manifest, ActivationDeclaration):
        raise UsageError(f"manifest must be an ActivationManifest, not {type(manifest).__name__}.")

    return createActivationPlan(
        planId=manifest.planId,
        entries=tuple(
            materializeActivationEntry(
                spec=entry,
                basePath=basePath,
            )
            for entry in manifest.entries
        ),
    )
