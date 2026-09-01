# file: backend/capabilities/runtime.py ; version: 4
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from backend.core.validation import requireExactNonBlankString
from backend.registration import Registration, RegistrationRegistry, RegistrationScope

__all__ = ["CapabilityHandler", "CapabilityRegistry"]

CapabilityHandler = Callable[[object, object | None], object]


@dataclass(frozen=True, slots=True)
class _Capability:
    handler: CapabilityHandler


def _requireCapabilityId(capabilityId: str) -> str:
    value = requireExactNonBlankString(capabilityId, "capabilityId")
    if value.count("@") != 1:
        raise ValueError("Capability identity must use exactly one name@major separator.")
    name, majorText = value.rsplit("@", 1)
    if not name:
        raise ValueError("Capability identity name must not be empty.")
    if not majorText.isascii() or not majorText.isdigit():
        raise ValueError("Capability major version must be an ASCII decimal integer.")
    major = int(majorText)
    if major <= 0 or str(major) != majorText:
        raise ValueError("Capability major version must be a canonical positive integer.")
    return value


class CapabilityRegistry:
    """Published cross-Pack capability registry with strict name@major identities."""

    def __init__(self) -> None:
        self._registry: RegistrationRegistry[_Capability] = RegistrationRegistry()

    def register(self, scope: RegistrationScope, *, ownerId: str, capabilityId: str, handler: CapabilityHandler) -> None:
        cleanCapabilityId = _requireCapabilityId(capabilityId)
        if not callable(handler):
            raise TypeError("handler must be callable.")
        scope.register(self._registry, ownerId=ownerId, name=cleanCapabilityId, value=_Capability(handler=handler))

    def resolve(self, capabilityId: str) -> Registration[_Capability]:
        return self._registry.require(_requireCapabilityId(capabilityId))

    def invokeResolved(self, registration: Registration[_Capability], *, context: object, payload: object | None = None) -> object:
        return registration.value.handler(context, payload)

    def unregisterOwnedBy(self, ownerId: str) -> None:
        self._registry.unregisterOwnedBy(ownerId)
