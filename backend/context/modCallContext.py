# file: backend/context/modCallContext.py
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.core.validation import requireExactNonBlankString

if TYPE_CHECKING:
    from backend.capabilities.registry import CapabilityHandler, CapabilityRegistry


class ModCallContext:
    """
    Context object for controlled Actant runtime participation.

    This context exposes the platform-approved surfaces available to runtime
    participants. It does not expose file I/O, memory, transactions, tracing,
    RuntimeHost, Workspace, ApplicationRun, SaveBundle, or arbitrary backend
    internals.
    """

    def __init__(
        self,
        *,
        ownerId: str,
        capabilityRegistry: CapabilityRegistry,
    ) -> None:
        self._ownerId = requireExactNonBlankString(ownerId, "ownerId")
        self._capabilityRegistry = capabilityRegistry

    @property
    def ownerId(self) -> str:
        return self._ownerId

    def registerCapability(
        self,
        capabilityId: str,
        handler: CapabilityHandler,
    ) -> None:
        self._capabilityRegistry.register(
            capabilityId=capabilityId,
            ownerId=self._ownerId,
            handler=handler,
        )
