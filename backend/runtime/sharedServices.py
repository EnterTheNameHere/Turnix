# file: backend/runtime/sharedServices.py ; version: 2
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "SharedServiceConflictError",
    "SharedServiceLease",
    "SharedServiceRegistry",
    "bindApplicationRunSharedServices",
    "sharedServicesForApplicationRun",
    "unbindApplicationRunSharedServices",
]


class SharedServiceConflictError(RuntimeError):
    """Reports incompatible attempts to share one host service identity."""


@dataclass(slots=True)
class _SharedServiceRecord[T]:
    """Owns one host service resource and its active lease count."""

    compatibilityKey: str
    resource: T
    closer: Callable[[T], None]
    leases: int = 0


class SharedServiceLease[T]:
    """Reference-counted claim on one RuntimeHost-owned shared service resource."""

    def __init__(self, *, registry: SharedServiceRegistry, serviceId: str, resource: T) -> None:
        """Binds one lease to its host registry and shared resource."""
        self._registry = registry
        self._serviceId = serviceId
        self.resource = resource
        self._released = False

    @property
    def released(self) -> bool:
        """Reports whether this lease has already relinquished its host claim."""
        return self._released

    def release(self) -> None:
        """Releases this claim and disposes the resource after its final lease."""
        if self._released:
            return
        self._released = True
        self._registry.releaseLease(self._serviceId)


class SharedServiceRegistry:
    """Owns cross-ApplicationRun host services without exposing provider policy.

    A service identity names one host-level resource. Multiple ApplicationRuns may
    lease the same resource only when they present the same compatibility key. The
    registry deliberately does not decide which model should be resident, which
    requester has priority, or whether a resource should be evicted under pressure;
    those are later resource-policy concerns.
    """

    def __init__(self) -> None:
        """Creates an empty active host service registry."""
        self._lane = RLock()
        self._records: dict[str, _SharedServiceRecord[object]] = {}
        self._closed = False

    def acquire[T](
        self,
        *,
        serviceId: str,
        compatibilityKey: str,
        factory: Callable[[], T],
        closer: Callable[[T], None],
    ) -> SharedServiceLease[T]:
        """Acquires a compatible host service, creating it on the first lease."""
        if type(serviceId) is not str or not serviceId:
            raise ValueError("serviceId must be a non-empty exact string.")
        if type(compatibilityKey) is not str or not compatibilityKey:
            raise ValueError("compatibilityKey must be a non-empty exact string.")
        if not callable(factory):
            raise TypeError("factory must be callable.")
        if not callable(closer):
            raise TypeError("closer must be callable.")

        with self._lane:
            if self._closed:
                raise RuntimeError("SharedServiceRegistry is closed.")
            existing = self._records.get(serviceId)
            if existing is None:
                resource = factory()
                record: _SharedServiceRecord[T] = _SharedServiceRecord(
                    compatibilityKey=compatibilityKey,
                    resource=resource,
                    closer=closer,
                    leases=1,
                )
                self._records[serviceId] = record
                return SharedServiceLease(registry=self, serviceId=serviceId, resource=resource)

            if existing.compatibilityKey != compatibilityKey:
                raise SharedServiceConflictError(
                    f"Shared host service {serviceId!r} is already active with incompatible configuration."
                )
            existing.leases += 1
            return SharedServiceLease(
                registry=self,
                serviceId=serviceId,
                resource=existing.resource,
            )

    def releaseLease(self, serviceId: str) -> None:
        """Drops one lease and closes a service when no ApplicationRun still claims it."""
        with self._lane:
            record = self._records.get(serviceId)
            if record is None:
                return
            record.leases -= 1
            if record.leases > 0:
                return
            del self._records[serviceId]
            record.closer(record.resource)

    def close(self) -> None:
        """Closes every remaining host service and rejects future acquisitions."""
        with self._lane:
            if self._closed:
                return
            self._closed = True
            records = tuple(self._records.values())
            self._records.clear()
        errors: list[Exception] = []
        for record in reversed(records):
            try:
                record.closer(record.resource)
            except Exception as err:  # noqa: BLE001 - host cleanup must collect arbitrary service failures.
                errors.append(err)
        if errors:
            raise ExceptionGroup("SharedServiceRegistry close reported errors.", errors)


_bindingLane = RLock()
_servicesByApplicationRunId: dict[str, SharedServiceRegistry] = {}


def bindApplicationRunSharedServices(
    applicationRunId: str,
    registry: SharedServiceRegistry,
) -> None:
    """Binds one live ApplicationRun identity to its owning host service registry."""
    if type(applicationRunId) is not str or not applicationRunId:
        raise ValueError("applicationRunId must be a non-empty exact string.")
    if not isinstance(registry, SharedServiceRegistry):
        raise TypeError("registry must be a SharedServiceRegistry.")
    with _bindingLane:
        existing = _servicesByApplicationRunId.get(applicationRunId)
        if existing is not None and existing is not registry:
            raise RuntimeError(
                f"ApplicationRun already belongs to another shared-service registry: {applicationRunId}."
            )
        _servicesByApplicationRunId[applicationRunId] = registry


def unbindApplicationRunSharedServices(applicationRunId: str) -> None:
    """Removes the process-local routing entry for one no-longer-hosted ApplicationRun."""
    if type(applicationRunId) is not str or not applicationRunId:
        raise ValueError("applicationRunId must be a non-empty exact string.")
    with _bindingLane:
        _servicesByApplicationRunId.pop(applicationRunId, None)


def sharedServicesForApplicationRun(applicationRunId: str) -> SharedServiceRegistry:
    """Returns the RuntimeHost-owned shared-service registry for a live ApplicationRun."""
    if type(applicationRunId) is not str or not applicationRunId:
        raise ValueError("applicationRunId must be a non-empty exact string.")
    with _bindingLane:
        try:
            return _servicesByApplicationRunId[applicationRunId]
        except KeyError as err:
            raise RuntimeError(
                f"ApplicationRun is not bound to RuntimeHost shared services: {applicationRunId}."
            ) from err
