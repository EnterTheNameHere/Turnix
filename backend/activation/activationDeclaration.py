# file: backend/activation/activationDeclaration.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.activation.activationMaterializer import materializeActivationEntry
from backend.activation.activationPlan import ActivationPlan, createActivationPlan
from backend.activation.activationSpec import ActivationSpec
from backend.core.errors import UsageError
from backend.core.validation import requireExactNonBlankString, typeName

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class ActivationDeclaration:
    """
    In-memory declaration-shaped activation group.

    This is not a Pack manifest, manifest file parsing, Pack discovery,
    dependency solving, version resolution, permission enforcement, or
    activation lifecycle state.
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
        raise UsageError(f"entries must be a tuple, not {typeName(entries)}.")

    if not entries:
        raise UsageError("entries must be non-empty.")

    for index, entry in enumerate(entries):
        if not isinstance(entry, ActivationSpec):
            raise UsageError(f"entries[{index}] must be an ActivationSpec, not {typeName(entry)}.")

    return ActivationDeclaration(
        planId=cleanPlanId,
        entries=tuple(entries),
    )


def materializeActivationPlan(
    *,
    declaration: ActivationDeclaration,
    basePath: Path,
) -> ActivationPlan:
    if not isinstance(declaration, ActivationDeclaration):
        raise UsageError(f"declaration must be an ActivationDeclaration, not {typeName(declaration)}.")

    return createActivationPlan(
        planId=declaration.planId,
        entries=tuple(
            materializeActivationEntry(
                spec=entry,
                basePath=basePath,
            )
            for entry in declaration.entries
        ),
    )
