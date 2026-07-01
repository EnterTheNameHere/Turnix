# file: backend/activation/activationPlan.py
from __future__ import annotations

from dataclasses import dataclass

from backend.activation.activationEntry import PythonActivationEntry
from backend.core.errors import UsageError


@dataclass(frozen=True)
class ActivationPlan:
    """
    Ordered activation input for platform-controlled activation.

    This is a small bootstrap activation plan shape. It is not PackManager graph
    preparation, dependency solving, activation lifecycle evidence, or full
    IS-11 activation plan evidence.
    """

    planId: str
    entries: tuple[PythonActivationEntry, ...]


def createActivationPlan(
    *,
    planId: str,
    entries: tuple[PythonActivationEntry, ...],
) -> ActivationPlan:
    planId = requireExactNonBlackString(planId, "planId")

    if not isinstance(entries, tuple):
        raise UsageError(f"entries must be a tuple, not {type(entries).__name__}.")

    if len(entries) == 0:
        raise UsageError("entries must contain at least one activation entry.")

    for index, entry in enumerate(entries):
        if not isinstance(entry, PythonActivationEntry):
            raise UsageError(f"entries[{index}] must be a PythonActivationEntry, not {type(entry).__name__}.")

    return ActivationPlan(
        planId=planId,
        entries=entries,
    )


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
