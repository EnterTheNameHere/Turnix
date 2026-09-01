# file: backend/capabilities/runtime.py ; version: 3
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from backend.registration import Registration, RegistrationRegistry, RegistrationScope

__all__ = ["CapabilityHandler", "CapabilityRegistry"]

CapabilityHandler = Callable[[object, object | None], object]


@dataclass(frozen=True, slots=True)
class _Capability:
    handler: CapabilityHandler


class CapabilityRegistry:
    """Published cross-Pack capability registry with name@major identities."""

    def __init__(self) -> None:
        self._registry: RegistrationRegistry[_Capability] = RegistrationRegistry()

    def register(self, scope: RegistrationScope, *, ownerId: str, capabilityId: str, handler: CapabilityHandler) -> None:
        if "@" not in capabilityId:
            raise ValueError("Capability identity must include @major.")
        if not callable(handler):
            raise TypeError("handler must be callable.")
        scope.register(self._registry, ownerId=ownerId, name=capabilityId, value=_Capability(handler=handler))

    def resolve(self, capabilityId: str) -> Registration[_Capability]:
        return self._registry.require(capabilityId)

    def invokeResolved(self, registration: Registration[_Capability], *, context: object, payload: object | None = None) -> object:
        return registration.value.handler(context, payload)

    def unregisterOwnedBy(self, ownerId: str) -> None:
        self._registry.unregisterOwnedBy(ownerId)
