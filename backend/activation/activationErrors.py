# file: backend/activation/activationErrors.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.core.errors import ActantError

if TYPE_CHECKING:
    from pathlib import Path

    from backend.activation.activationAdapterKind import ActivationAdapterKind


@dataclass(frozen=True)
class ActivationFailureContext:
    """
    Activation-specific context for a failed activation entry.

    This is not persisted activation evidence, rollback state, trace proof, or
    full lifecycle failure reporting.
    """

    planId: str
    entryId: str
    ownerId: str
    adapterKind: ActivationAdapterKind
    sourcePath: Path
    callableName: str


class ActivationError(ActantError):
    """
    Raised when activation of a plan entry fails.

    This wraps the underlying exception with activation-specific context.
    """

    def __init__(
        self,
        *,
        context: ActivationFailureContext,
        cause: BaseException,
    ) -> None:
        self.context = context
        self.cause = cause

        super().__init__(
            "Activation failed for "
            f"planId={context.planId} "
            f"entryId={context.entryId} "
            f"ownerId={context.ownerId} "
            f"adapterKind={context.adapterKind} "
            f"sourcePath={context.sourcePath} "
            f"callableName={context.callableName} "
            f"{type(cause).__name__}: {cause}"
        )
