# file: backend/registration/registry.py ; version: 2
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Generic, TypeVar

from backend.core.validation import requireExactNonBlankString

__all__ = ["Registration", "RegistrationRegistry", "RegistrationScope"]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Registration(Generic[T]):
    """One immutable runtime registration owned by one CodeEntry incarnation."""

    name: str
    ownerId: str
    value: T


class RegistrationRegistry(Generic[T]):
    """Stores published registrations; provisional state lives in RegistrationScope."""

    def __init__(self) -> None:
        self._published: dict[str, Registration[T]] = {}
        self._lock = RLock()

    def require(self, name: str) -> Registration[T]:
        cleanName = requireExactNonBlankString(name, "name")
        with self._lock:
            try:
                return self._published[cleanName]
            except KeyError as err:
                raise LookupError(f"Registration is not published: {cleanName}.") from err

    def snapshot(self) -> tuple[Registration[T], ...]:
        with self._lock:
            return tuple(self._published[name] for name in sorted(self._published))

    def _canPublish(self, registration: Registration[T]) -> None:
        with self._lock:
            existing = self._published.get(registration.name)
            if existing is not None:
                raise RuntimeError(
                    f"Registration {registration.name!r} is already published by {existing.ownerId!r}.",
                )

    def _publish(self, registration: Registration[T]) -> None:
        with self._lock:
            self._canPublish(registration)
            self._published[registration.name] = registration

    def _removeExact(self, registration: Registration[T]) -> None:
        with self._lock:
            if self._published.get(registration.name) is registration:
                self._published.pop(registration.name, None)

    def unregisterOwnedBy(self, ownerId: str) -> tuple[Registration[T], ...]:
        cleanOwnerId = requireExactNonBlankString(ownerId, "ownerId")
        with self._lock:
            names = sorted(name for name, item in self._published.items() if item.ownerId == cleanOwnerId)
            return tuple(self._published.pop(name) for name in names)


class RegistrationScope:
    """One activation publication boundary spanning every CodeEntry in a Pack."""

    def __init__(self) -> None:
        self._pending: list[tuple[RegistrationRegistry[object], Registration[object]]] = []
        self._resolved = False

    def register(self, registry: RegistrationRegistry[T], *, ownerId: str, name: str, value: T) -> Registration[T]:
        if self._resolved:
            raise RuntimeError("RegistrationScope is already resolved.")
        registration = Registration(
            name=requireExactNonBlankString(name, "name"),
            ownerId=requireExactNonBlankString(ownerId, "ownerId"),
            value=value,
        )
        self._pending.append((registry, registration))
        return registration

    def publish(self) -> None:
        if self._resolved:
            raise RuntimeError("RegistrationScope is already resolved.")
        # Preflight every name before anything becomes externally visible.
        seen: set[tuple[int, str]] = set()
        for registry, registration in self._pending:
            key = (id(registry), registration.name)
            if key in seen:
                raise RuntimeError(f"Duplicate provisional registration {registration.name!r} in one activation scope.")
            seen.add(key)
            registry._canPublish(registration)
        published: list[tuple[RegistrationRegistry[object], Registration[object]]] = []
        try:
            for registry, registration in self._pending:
                registry._publish(registration)
                published.append((registry, registration))
        except Exception:
            for registry, registration in reversed(published):
                registry._removeExact(registration)
            raise
        self._resolved = True
        self._pending.clear()

    def withdraw(self) -> None:
        if not self._resolved:
            self._resolved = True
            self._pending.clear()
