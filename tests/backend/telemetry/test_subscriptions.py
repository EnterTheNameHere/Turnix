# file: tests/backend/telemetry/test_subscriptions.py ; version: 1
from __future__ import annotations

import importlib
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from backend.telemetry import (
    SignalUnavailable,
    SystemMemoryObservation,
    TelemetryConfiguration,
    TelemetryMode,
    TelemetryService,
    createDefaultTelemetryService,
)


class Clock:
    """Separates deterministic monotonic collection evidence from jumping wall time."""

    def __init__(self) -> None:
        """Starts sufficiently far from zero to expose accidental epoch alignment."""
        self.now = 10_000_000_000
        self.wall = 1_800_000_000_000_000_000

    def monotonic(self) -> int:
        """Returns host-local monotonic nanoseconds without advancing implicitly."""
        return self.now

    def wallTime(self) -> int:
        """Returns independent display time, which tests may move backwards."""
        return self.wall


class Machine:
    """Counts memory collections and simulates a measurable sequential read delay."""

    def __init__(self, clock: Clock) -> None:
        """Binds fake time and initializes collection/failure evidence."""
        self.clock = clock
        self.reads = 0
        self.fail = False
        self.entered: Event | None = None
        self.resume: Event | None = None

    def systemMemory(self) -> SystemMemoryObservation:
        """Reads once, optionally blocking or failing without requiring real hardware."""
        self.reads += 1
        if self.entered is not None:
            self.entered.set()
            assert self.resume.wait(5)
        self.clock.now += 100_000_000
        if self.fail:
            raise LookupError("optional provider failed")
        return SystemMemoryObservation(100, 60, 40, 20, 40.0)

    def swap(self) -> SignalUnavailable:
        """Represents a missing optional signal without a numeric placeholder."""
        return SignalUnavailable("swap unsupported")


class Gpu:
    """Tracks service-owned provider initialization, reads and final resource closure."""

    def __init__(self) -> None:
        """Initializes deterministic resource evidence."""
        self.reads = 0
        self.closes = 0

    def gpu(self) -> SignalUnavailable:
        """Returns explicit absence of a usable sensor rather than fake load."""
        self.reads += 1
        return SignalUnavailable("sensor unsupported")

    def close(self) -> None:
        """Records every attempted resource release."""
        self.closes += 1


def configuration(interval: float = 1000, *, samples: int = 2) -> TelemetryConfiguration:
    """Requests bounded memory history without touching optional CPU/GPU groups."""
    return TelemetryConfiguration(
        memoryMode=TelemetryMode.HISTORY,
        cpuMode=TelemetryMode.DISABLED,
        gpuMode=TelemetryMode.DISABLED,
        sampleIntervalSeconds=interval,
        historySamples=samples,
    )


def setupService() -> tuple[TelemetryService, Machine, Clock]:
    """Builds a fake-clock service whose long cadence avoids wall-time test sleeps."""
    clock = Clock()
    machine = Machine(clock)
    service = TelemetryService(
        machineProvider=machine, gpuProvider=None, configuration=configuration(),
        monotonicNs=clock.monotonic, wallTimeNs=clock.wallTime,
    )
    return service, machine, clock


def test_mixed_cadences_share_one_collector_and_bound_independent_histories() -> None:
    """A slow subscriber reuses shared reads and retains only its own delivery cadence."""
    service, machine, clock = setupService()
    with service.owner() as owner:
        fast = owner.subscribe(configuration(1000, samples=3))
        worker = service._thread
        slow = owner.subscribe(configuration(2000))
        assert machine.reads == 1
        assert service._thread is worker
        assert service.collectionIntervalSeconds == 1000
        for _ in range(6):
            clock.now += 1_000_000_000_000
            service.sample()
        assert machine.reads == 7
        assert len(fast.history) == 3
        assert len(slow.history) == 2
        assert slow.history[-1].collectionStartNs - slow.history[-2].collectionStartNs >= 2_000_000_000_000
        fast.release()
        assert service.collectionIntervalSeconds == 2000
        assert slow.current is not None
    assert slow.released
    assert service.collectionIntervalSeconds is None


@pytest.mark.parametrize("reverse", [False, True])
def test_release_order_duplicate_release_and_owner_teardown(reverse: bool) -> None:
    """Revocation affects only the targeted claim, including either release order."""
    service, _, _ = setupService()
    firstOwner = service.owner()
    secondOwner = service.owner()
    first = firstOwner.subscribe(configuration())
    second = secondOwner.subscribe(configuration())
    left, right = (second, first) if reverse else (first, second)
    left.release()
    left.release()
    assert left.current is None and left.history == ()
    assert right.current is not None and not right.released
    firstOwner.release()
    secondOwner.release()
    secondOwner.release()
    assert service._latest is None
    assert not service._subscriptions
    with pytest.raises(RuntimeError, match="released"):
        secondOwner.subscribe(configuration())


def test_existing_consumer_survives_monitoring_release_and_inverse() -> None:
    """Legacy polling and retained data are independent claims in both teardown orders."""
    service, _, clock = setupService()
    service.start()
    subscription = service.subscribe(configuration(500))
    subscription.release()
    assert service.collectionIntervalSeconds == 1000
    assert service.current is not None and len(service.history) == 1
    subscription = service.subscribe(configuration(500))
    service.stop()
    assert not subscription.released
    assert service.collectionIntervalSeconds == 500
    clock.now += 500_000_000_000
    service.sample()
    assert len(subscription.history) == 2
    subscription.release()
    assert service.collectionIntervalSeconds is None


def test_last_owned_claim_closes_only_owned_provider_and_reacquires() -> None:
    """Factory providers close once after final use and are reconstructed on reacquisition."""
    created = []

    def factory() -> Gpu:
        """Creates a new closable fake GPU for each separate active lifetime."""
        gpu = Gpu()
        created.append(gpu)
        return gpu

    config = TelemetryConfiguration(
        memoryMode=TelemetryMode.DISABLED, cpuMode=TelemetryMode.DISABLED,
        gpuMode=TelemetryMode.HISTORY, sampleIntervalSeconds=1000,
    )
    service = TelemetryService(machineProvider=None, gpuProvider=None, gpuProviderFactory=factory)
    first = service.subscribe(config)
    second = service.subscribe(config)
    first.release()
    assert created[0].closes == 0
    second.release()
    second.release()
    assert created[0].closes == 1
    assert first.history == second.history == ()
    with service.subscribe(config):
        assert len(created) == 2 and created[1].reads == 1
    assert created[1].closes == 1


def test_borrowed_provider_survives_monitoring_and_legacy_stop_is_deferred() -> None:
    """Monitoring cannot close externally supplied resources still used by another claim."""
    gpu = Gpu()
    service = TelemetryService(machineProvider=None, gpuProvider=gpu)
    with service.subscribe():
        pass
    assert gpu.closes == 0
    service.start()
    subscription = service.subscribe()
    service.stop()
    assert gpu.closes == 0
    subscription.release()
    assert gpu.closes == 1


def test_missing_and_failing_providers_stay_explicit_and_retry_after_release() -> None:
    """Unexpected optional provider errors do not kill collection or fabricate zero load."""
    service, machine, clock = setupService()
    machine.fail = True
    with service.subscribe(configuration()) as subscription:
        assert isinstance(subscription.current.systemMemory, SignalUnavailable)
        machine.fail = False
        clock.now += 1_000_000_000_000
        service.sample()
        assert isinstance(subscription.current.systemMemory, SystemMemoryObservation)
    attempts = []

    def failingFactory() -> Gpu:
        """Models an installed but unusable optional hardware provider."""
        attempts.append(True)
        raise ImportError("no optional driver")

    service = TelemetryService(machineProvider=None, gpuProvider=None, gpuProviderFactory=failingFactory)
    for _ in range(2):
        with service.subscribe() as subscription:
            assert isinstance(subscription.current.gpu, SignalUnavailable)
            assert isinstance(subscription.current.systemMemory, SignalUnavailable)
    assert len(attempts) == 2


def test_no_consumers_or_enabled_groups_do_not_load_optional_providers(monkeypatch) -> None:
    """Construction and all-disabled subscriptions perform no vendor import or device work."""
    imports = []

    def forbiddenImport(name: str) -> None:
        """Fails visibly if an inactive request imports an optional hardware module."""
        imports.append(name)
        raise AssertionError("inactive provider import")

    monkeypatch.setattr("backend.telemetry.providers.import_module", forbiddenImport)
    importlib.reload(importlib.import_module("backend.telemetry"))
    service = createDefaultTelemetryService()
    assert service._thread is None and service.current is None
    disabled = TelemetryConfiguration(
        memoryMode=TelemetryMode.DISABLED, cpuMode=TelemetryMode.DISABLED, gpuMode=TelemetryMode.DISABLED,
    )
    with service.subscribe(disabled) as subscription:
        assert service._thread is None
        assert isinstance(subscription.current.gpu, SignalUnavailable)
    assert imports == []


def test_collection_intervals_and_wall_jumps_survive_history_projection() -> None:
    """Collection duration and actual spacing use monotonic time even as display time jumps."""
    service, _, clock = setupService()
    with service.subscribe(configuration()) as subscription:
        first = subscription.current
        assert first.collectionEndNs - first.collectionStartNs == 100_000_000
        clock.wall -= 10_000_000_000
        clock.now += 1_000_000_000_000
        service.sample()
        second = subscription.current
        assert second.sampledTimeNs < first.sampledTimeNs
        assert second.collectionStartNs > first.collectionEndNs
        assert second.actualIntervalSeconds == (second.collectionStartNs - first.collectionStartNs) / 1e9
        assert second.sampleIntervalSeconds == 1000
        assert subscription.history[-1] == second
        assert isinstance(second.cpuUtilization, SignalUnavailable)


def test_concurrent_release_waits_for_sample_then_prevents_publication() -> None:
    """In-flight collection and revocation serialize; a released claim cannot receive later data."""
    service, machine, clock = setupService()
    subscription = service.subscribe(configuration())
    machine.entered, machine.resume = Event(), Event()
    clock.now += 1_000_000_000_000
    releaseStarted = Event()

    def release() -> None:
        """Signals attempted revocation before waiting for the collection lane."""
        releaseStarted.set()
        subscription.release()

    with ThreadPoolExecutor(max_workers=2) as executor:
        sampling = executor.submit(service.sample)
        assert machine.entered.wait(5)
        releasing = executor.submit(release)
        assert releaseStarted.wait(5)
        assert not releasing.done()
        machine.resume.set()
        sampling.result(timeout=5)
        releasing.result(timeout=5)
    assert subscription.current is None and subscription.history == ()
    assert service._latest is None
    worker = service._thread
    if worker is not None:
        worker.join(timeout=5)
        assert not worker.is_alive()


def test_legacy_history_is_downsampled_when_monitoring_is_faster() -> None:
    """Faster monitoring must not shorten the configured legacy history horizon."""
    service, _, clock = setupService()
    service.start()
    with service.subscribe(configuration(500)):
        with service._condition:
            clock.now += 500_000_000_000
            service._collect()  # Same delivery path as the polling worker.
            assert len(service.history) == 1
            clock.now += 500_000_000_000
            service._collect()
            assert len(service.history) == 2
    service.stop()


def test_generic_import_has_no_optional_vendor_dependency() -> None:
    """A fresh interpreter can request unavailable hardware without loading vendor modules."""
    script = """
import sys
from backend.telemetry import TelemetryService, createDefaultTelemetryService
service = createDefaultTelemetryService()
assert 'psutil' not in sys.modules
assert 'pynvml' not in sys.modules
with TelemetryService(machineProvider=None, gpuProvider=None).subscribe():
    assert 'psutil' not in sys.modules
    assert 'pynvml' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-X", "frozen_modules=off", "-c", script],
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_background_collection_stops_and_reacquires_without_duplicate_workers() -> None:
    """Real polling stops at the final claim and restarts cleanly with one worker."""
    service, machine, _ = setupService()
    service._monotonicNs = time.monotonic_ns
    for _ in range(2):
        collected = Event()

        def observedRead() -> SignalUnavailable:
            """Signals that the worker collected after synchronous subscription setup."""
            collected.set()
            return SignalUnavailable("no swap")

        with service.subscribe(configuration(0.01)) as subscription:
            machine.swap = observedRead
            assert collected.wait(5)
            worker = service._thread
            assert worker is not None
            assert subscription.current is not None
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert service._thread is None and service._latest is None


def test_group_release_closes_unused_owned_gpu_while_memory_continues() -> None:
    """Removing the final GPU requirement stops that provider even if other groups remain."""
    gpu = Gpu()
    service, _, _ = setupService()
    def factory() -> Gpu:
        """Supplies the explicitly composed optional GPU without real hardware."""
        return gpu

    service._gpuFactory = factory
    memory = service.subscribe(configuration())
    gpuClaim = service.subscribe(TelemetryConfiguration(sampleIntervalSeconds=1000))
    service.sample()
    assert gpu.reads == 1
    gpuClaim.release()
    assert gpu.closes == 1
    service.sample()
    assert gpu.reads == 1 and memory.current is not None
    memory.release()


def test_new_group_delivers_on_next_read_and_disabled_claim_retains_no_hardware() -> None:
    """Joining slower groups receive the next shared read without waiting a full slow interval."""
    service, _, clock = setupService()
    disabled = service.subscribe(TelemetryConfiguration(
        memoryMode=TelemetryMode.DISABLED, cpuMode=TelemetryMode.DISABLED, gpuMode=TelemetryMode.DISABLED,
    ))
    memory = service.subscribe(configuration(1000))
    with service._condition:
        clock.now += 1_000_000_000_000
        service._collect()
    assert isinstance(memory.current.systemMemory, SystemMemoryObservation)
    memory.release()
    assert service._latest is None and service.collectionIntervalSeconds is None
    assert isinstance(disabled.current.systemMemory, SignalUnavailable)
    disabled.release()


def test_failed_gpu_retries_after_its_last_claim_even_with_other_groups_alive() -> None:
    """Unavailable GPU lifetime can end independently of a surviving memory consumer."""
    service, _, _ = setupService()
    attempts = []

    def factory() -> Gpu:
        """Fails once, then models a now-available optional driver."""
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("driver unavailable")
        return Gpu()

    service._gpuFactory = factory
    with service.subscribe(configuration()):
        for _ in range(2):
            with service.subscribe(TelemetryConfiguration(sampleIntervalSeconds=1000)):
                service.sample()
    assert len(attempts) == 2


@pytest.mark.parametrize("interval", [float("nan"), float("inf"), 0, -1, True, 1e-20, 1e308])
def test_invalid_cadence_is_rejected(interval: float) -> None:
    """Non-finite and non-positive requests cannot break shared polling deadlines."""
    with pytest.raises(ValueError, match="finite and positive"):
        configuration(interval)
