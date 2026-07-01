# file: backend/capabilities/registry.py
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from backend.core.errors import UsageError


type CapabilityHandler = Callable[[object | None], object]


@dataclass(frozen=True)
class RegisteredCapability:
    """
    One capability registered with the platform registry.

    ownerId is intentionally plain text in this bootstrap step. It is not yet a
    final Pack id, activation id, or provider id.
    """

    capabilityId: str
    ownerId: str
    handler: CapabilityHandler


class CapabilityRegistry:
    """
    Minimal in-memory capability registry.

    This is not full DA-16 capability infrastructure. It has no permissions,
    provider priority, dependency solving, remote transport, streaming,
    persistence, or schema validation.
    """

    def __init__(self) -> None:
        self._capabilities: dict[str, RegisteredCapability] = {}

    def register(
        self,
        *,
        capabilityId: str,
        ownerId: str,
        handler: CapabilityHandler,
    ) -> None:
        cleanCapabilityId = requireNonEmptyText(capabilityId, "capabilityId")
        cleanOwnerId = requireNonEmptyText(ownerId, "ownerId")

        if not callable(handler):
            raise UsageError(f"Capability handler for {cleanCapabilityId} must be callable")

        if cleanCapabilityId in self._capabilities:
            registered = self._capabilities[cleanCapabilityId]
            raise UsageError(f"Capability {cleanCapabilityId} handler is already registered by {registered.ownerId}")

        self._capabilities[cleanCapabilityId] = RegisteredCapability(
            capabilityId=cleanCapabilityId,
            ownerId=cleanOwnerId,
            handler=handler,
        )

    def has(self, capabilityId: str) -> bool:
        cleanCapabilityId = requireNonEmptyText(capabilityId, "capabilityId")
        return cleanCapabilityId in self._capabilities

    def get(self, capabilityId: str) -> RegisteredCapability:
        cleanCapabilityId = requireNonEmptyText(capabilityId, "capabilityId")

        try:
            return self._capabilities[cleanCapabilityId]
        except KeyError as err:
            raise UsageError(f"Capability {cleanCapabilityId} handler is not registered.") from err

    def call(self, capabilityId: str, payload: object | None = None) -> object:
        registeredCapability = self.get(capabilityId)
        return registeredCapability.handler(payload)


def requireNonEmptyText(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise UsageError(f"{name} must be a string.")

    cleanValue = value.strip()
    if not cleanValue:
        raise UsageError(f"{name} must not be an empty string.")

    return cleanValue
