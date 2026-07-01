# file: backend/activation/activationReport.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.activation.activationAdapterKind import ActivationAdapterKind


@dataclass(frozen=True)
class ActivatedEntry:
    """
    One successfully completed activation entry.

    This is successful activation output only. It is not persisted activation
    evidence, rollback state, failure reporting, or full lifecycle reporting.
    """

    entryId: str
    ownerId: str
    adapterKind: ActivationAdapterKind
    sourcePath: Path
    callableName: str


@dataclass(frozen=True)
class ActivationReport:
    """
    Report returned by successful activation plan execution.

    This report records completed entries only. Failed activation still raises
    ActivationError instead of returning a partial report.
    """

    planId: str
    activatedEntries: tuple[ActivatedEntry, ...]

    @property
    def activatedEntryIds(self) -> tuple[str, ...]:
        return tuple(entry.entryId for entry in self.activatedEntries)
