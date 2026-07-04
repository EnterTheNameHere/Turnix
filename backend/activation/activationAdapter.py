# file: backend/activation/activationAdapter.py
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from backend.activation.activationEntry import PythonActivationEntry
    from backend.context.modCallContext import ModCallContext


class ActivationAdapter(Protocol):
    """
    Protocol for activation adapters used by activation plan execution.

    This is the minimum runtime contract required by activatePlan. It does not
    define adapter lifecycle, process ownership, sandboxing, permissions,
    streaming, unloading, reloading, or manifest parsing.
    """

    def loadAndCall(
        self,
        *,
        entry: PythonActivationEntry,
        ctx: ModCallContext,
    ) -> object:
        ...
