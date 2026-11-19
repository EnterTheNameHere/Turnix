# file: backend/registration/registry.py ; version: 3
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Generic, TypeVar

from backend.core.validation import requireExactNonBlankString

__all__ = ["Registration", "RegistrationRegistry", "RegistrationScope"]

T = TypeVar("T")
_PUBLICATION_LOCK = RLock()


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

    def _canPublishUnlocked(self, registration: Registration[T]) -> None:
        existing = self._published.get(registration.name)
        if existing is not None:
            raise RuntimeError(
                f"Registration {registration.name!r} is already published by {existing.ownerId!r}.",
            )

    def _publishUnlocked(self, registration: Registration[T]) -> None:
        self._canPublishUnlocked(registration)
        self._published[registration.name] = registration

    def _removeExactUnlocked(self, registration: Registration[T]) -> None:
        if self._published.get(registration.name) is registration:
            self._published.pop(registration.name, None)

    def unregisterOwnedBy(self, ownerId: str) -> tuple[Registration[T], ...]:
        cleanOwnerId = requireExactNonBlankString(ownerId, "ownerId")
        with _PUBLICATION_LOCK, self._lock:
            names = sorted(name for name, item in self._published.items() if item.ownerId == cleanOwnerId)
            return tuple(self._published.pop(name) for name in names)


class RegistrationScope:
    """One atomic publication and withdrawal boundary for runtime registrations.

    Registrations remain provisional until publish() succeeds. Publication of
    the complete scope is serialized across all RegistrationRegistry instances,
    preventing another publisher or owner cleanup from invalidating a preflight
    between validation and publication.

    A published scope retains the exact registration objects it published.
    withdraw() therefore removes only those registrations and remains safe if a
    later registration reuses the same name.
    """

    def __init__(self) -> None:
        self._pending: list[tuple[RegistrationRegistry[object], Registration[object]]] = []
        self._published: tuple[tuple[RegistrationRegistry[object], Registration[object]], ...] = ()
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

        seen: set[tuple[int, str]] = set()
        registryOrder = sorted({registry for registry, _ in self._pending}, key=id)

        with _PUBLICATION_LOCK:
            for registry in registryOrder:
                registry._lock.acquire()
            try:
                for registry, registration in self._pending:
                    key = (id(registry), registration.name)
                    if key in seen:
                        raise RuntimeError(
                            f"Duplicate provisional registration {registration.name!r} in one activation scope.",
                        )
                    seen.add(key)
                    registry._canPublishUnlocked(registration)

                for registry, registration in self._pending:
                    registry._publishUnlocked(registration)

                self._published = tuple(self._pending)
                self._pending.clear()
                self._resolved = True
            finally:
                for registry in reversed(registryOrder):
                    registry._lock.release()

    def withdraw(self) -> None:
        if not self._resolved:
            self._pending.clear()
            self._resolved = True
            return
        if not self._published:
            return

        registryOrder = sorted({registry for registry, _ in self._published}, key=id)
        with _PUBLICATION_LOCK:
            for registry in registryOrder:
                registry._lock.acquire()
            try:
                for registry, registration in reversed(self._published):
                    registry._removeExactUnlocked(registration)
                self._published = ()
            finally:
                for registry in reversed(registryOrder):
                    registry._lock.release()
