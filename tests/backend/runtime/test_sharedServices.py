# file: tests/backend/runtime/test_sharedServices.py ; version: 1
from __future__ import annotations

import pytest

from backend.runtime.sharedServices import (
    SharedServiceConflictError,
    SharedServiceRegistry,
    bindApplicationRunSharedServices,
    sharedServicesForApplicationRun,
    unbindApplicationRunSharedServices,
)


class _Resource:
    """Minimal close-observable shared resource used by registry tests."""

    def __init__(self) -> None:
        """Creates an open fake resource."""
        self.closeCalls = 0

    def close(self) -> None:
        """Records one disposal request."""
        self.closeCalls += 1


def test_registry_reuses_compatible_service_until_final_lease_releases() -> None:
    """Compatible ApplicationRun claims share one resource and close it only once."""
    registry = SharedServiceRegistry()
    created: list[_Resource] = []

    def factory() -> _Resource:
        """Creates and records one fake host resource."""
        resource = _Resource()
        created.append(resource)
        return resource

    first = registry.acquire(
        serviceId="test.shared",
        compatibilityKey="same",
        factory=factory,
        closer=lambda resource: resource.close(),
    )
    second = registry.acquire(
        serviceId="test.shared",
        compatibilityKey="same",
        factory=factory,
        closer=lambda resource: resource.close(),
    )

    assert len(created) == 1
    assert first.resource is second.resource
    first.release()
    assert created[0].closeCalls == 0
    second.release()
    assert created[0].closeCalls == 1
    second.release()
    assert created[0].closeCalls == 1


def test_registry_rejects_incompatible_configuration_for_same_service() -> None:
    """One host service identity cannot silently represent incompatible resources."""
    registry = SharedServiceRegistry()
    resource = _Resource()
    lease = registry.acquire(
        serviceId="test.shared",
        compatibilityKey="one",
        factory=lambda: resource,
        closer=lambda value: value.close(),
    )
    with pytest.raises(SharedServiceConflictError, match="incompatible configuration"):
        registry.acquire(
            serviceId="test.shared",
            compatibilityKey="two",
            factory=_Resource,
            closer=lambda value: value.close(),
        )
    lease.release()


def test_registry_close_disposes_live_services_and_rejects_new_acquisition() -> None:
    """RuntimeHost shutdown can release remaining services even with outstanding leases."""
    registry = SharedServiceRegistry()
    resource = _Resource()
    lease = registry.acquire(
        serviceId="test.shared",
        compatibilityKey="same",
        factory=lambda: resource,
        closer=lambda value: value.close(),
    )

    registry.close()
    assert resource.closeCalls == 1
    lease.release()
    assert resource.closeCalls == 1
    with pytest.raises(RuntimeError, match="closed"):
        registry.acquire(
            serviceId="other",
            compatibilityKey="same",
            factory=_Resource,
            closer=lambda value: value.close(),
        )


def test_application_run_binding_routes_to_exact_host_registry() -> None:
    """CodeEntry lookup resolves the registry explicitly bound by RuntimeHost ownership."""
    registry = SharedServiceRegistry()
    bindApplicationRunSharedServices("run-a", registry)
    try:
        assert sharedServicesForApplicationRun("run-a") is registry
    finally:
        unbindApplicationRunSharedServices("run-a")

    with pytest.raises(RuntimeError, match="not bound"):
        sharedServicesForApplicationRun("run-a")
