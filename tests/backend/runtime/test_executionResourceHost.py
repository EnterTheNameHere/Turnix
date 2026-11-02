# file: tests/backend/runtime/test_executionResourceHost.py ; version: 1
"""Host composition tests proving cleanup after the host lock is released."""
from concurrent.futures import Future
from threading import Thread

import pytest

from backend.packs.runtime import PackResolver
from backend.runtime import RuntimeHost, RuntimeHostState
from backend.runtime.executionResources import (
    EffectStatus, Materialization, ProviderEffect, Quiescence, ResidencyDescriptor,
    ResourceCleanupError, ResourceState,
)
from backend.save import ApplicationStore

OK = ProviderEffect(EffectStatus.ACKNOWLEDGED, Quiescence.CONFIRMED)
UNKNOWN = ProviderEffect(EffectStatus.UNKNOWN, Quiescence.UNKNOWN)


def _threadResult(callback):
    """Requires a different thread to finish, bounding only deadlock detection."""
    result = Future()

    def invoke():
        """Preserves worker exceptions for assertions in the test thread."""
        try:
            result.set_result(callback())
        except BaseException as error:
            result.set_exception(error)

    Thread(target=invoke, daemon=True).start()
    return result.result(5)


class _HostAdapter:
    """Requires host/service/domain read entry from another thread on release."""

    def __init__(self, host):
        """Records observed reads, releases and configurable disposal evidence."""
        self.host = host
        self.domain = None
        self.releases = 0
        self.reads = []
        self.releaseEffect = OK

    def materialize(self, descriptor, token):
        """Acknowledges a fake object without creating any provider process."""
        return Materialization(OK, True, object())

    def release(self, token, payload):
        """Cannot acknowledge if the calling thread still holds a host/core lock."""
        self.releases += 1
        self.reads.append(_threadResult(lambda: (
            self.host.activeApplicationRuntimes, self.domain.snapshot, self.host.executionResources.snapshots)))
        return self.releaseEffect


def _fixture(tmp_path):
    """Constructs the real inert host and a trusted fake domain binding."""
    host = RuntimeHost(applicationStore=ApplicationStore(tmp_path / "saves"),
                       packResolver=PackResolver(roots=()))
    adapter = _HostAdapter(host)
    domain = host.executionResources.registerDomain("resource", compatibilityKey=b"binding", adapter=adapter)
    adapter.domain = domain
    profile = domain.registerProfile(ResidencyDescriptor(b"profile", object()))
    owner = domain.openParticipant(allowedProfiles=(profile,), allowIdleTransition=True)
    owner.transitionIdle(profile)
    return host, adapter, domain, profile, owner


def test_host_stop_releases_outside_host_service_and_domain_locks(tmp_path):
    """A second thread must enter read paths before release can complete."""
    host, adapter, domain, _, _ = _fixture(tmp_path)
    host.stop()
    assert host.state is RuntimeHostState.STOPPED
    assert domain.snapshot.state is ResourceState.CLOSED
    assert adapter.releases == 1 and len(adapter.reads) == 1
    host.stop()
    assert adapter.releases == 1


def test_pending_host_stop_retries_drain_after_late_operation_and_pin_finish(tmp_path):
    """STOPPED closes admission but does not imply that outstanding work drained."""
    host, adapter, domain, profile, owner = _fixture(tmp_path)
    claim = owner.acquire(profile)
    stops, disposals = [], []

    def stop():
        """Proves stop callbacks also run without the host lock."""
        stops.append(_threadResult(lambda: host.activeApplicationRuntimes))

    def dispose(payload):
        """Retained cleanup remains reachable after owner and host admission close."""
        disposals.append(_threadResult(lambda: (host.activeApplicationRuntimes, domain.snapshot)))
        return OK

    claim.bindStop(stop)
    claim.retain(object(), dispose)
    with pytest.raises(ExceptionGroup) as first:
        host.stop()
    assert any(isinstance(error, ResourceCleanupError) for error in first.value.exceptions)
    assert host.state is RuntimeHostState.STOPPED
    assert len(stops) == 1 and disposals == [] and adapter.releases == 0
    with pytest.raises(ExceptionGroup):
        host.stop()
    assert len(stops) == 1
    claim.finish(OK)
    assert len(disposals) == 1
    host.stop()
    assert adapter.releases == 1 and domain.snapshot.state is ResourceState.CLOSED


def test_unknown_shutdown_reports_on_every_stop_without_repeating_disposal(tmp_path):
    """Unknown release is a retained tombstone, never silently skipped after STOPPED."""
    host, adapter, domain, _, _ = _fixture(tmp_path)
    adapter.releaseEffect = UNKNOWN
    for _ in range(2):
        with pytest.raises(ExceptionGroup) as failure:
            host.stop()
        error = next(error for error in failure.value.exceptions if isinstance(error, ResourceCleanupError))
        assert error.snapshots[0].state is ResourceState.UNKNOWN
    assert adapter.releases == 1
    assert domain.snapshot.state is ResourceState.UNKNOWN
    with pytest.raises(RuntimeError):
        host.executionResources.registerDomain("resource", compatibilityKey=b"binding", adapter=adapter)


def test_legacy_cleanup_error_is_aggregated_with_new_resource_outcome(tmp_path, monkeypatch):
    """A legacy cleanup exception cannot skip outside-lock resource finalization."""
    host, adapter, domain, _, _ = _fixture(tmp_path)
    adapter.releaseEffect = UNKNOWN

    def fail():
        """Simulates an existing service closer failure without changing its API."""
        raise ValueError("legacy cleanup")

    monkeypatch.setattr(host.sharedServices, "close", fail)
    with pytest.raises(ExceptionGroup) as failure:
        host.stop()
    assert any(isinstance(error, ValueError) for error in failure.value.exceptions)
    assert any(isinstance(error, ResourceCleanupError) for error in failure.value.exceptions)
    assert adapter.releases == 1
    assert domain.snapshot.state is ResourceState.UNKNOWN


def test_legacy_teardown_completion_defers_disposer_until_host_lock_is_released(tmp_path, monkeypatch):
    """Late completion inside legacy teardown cannot dispatch new provider callbacks."""
    host, adapter, domain, profile, owner = _fixture(tmp_path)
    claim = owner.acquire(profile)
    disposed = []

    def dispose(payload):
        """Proves deferred cleanup occurs outside the host lock with another thread."""
        disposed.append(_threadResult(lambda: (host.activeApplicationRuntimes, domain.snapshot)))
        return OK

    claim.retain(object(), dispose)
    originalClose = host.sharedServices.close

    def closeLegacyServices():
        """Reports execution completion synchronously while the host lane is held."""
        claim.finish(OK)
        assert disposed == []
        originalClose()

    monkeypatch.setattr(host.sharedServices, "close", closeLegacyServices)
    host.stop()
    assert len(disposed) == 1 and adapter.releases == 1
    assert domain.snapshot.state is ResourceState.CLOSED
