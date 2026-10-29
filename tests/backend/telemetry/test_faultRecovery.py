# file: tests/backend/telemetry/test_faultRecovery.py ; version: 1
"""Deterministic acquisition rollback and actual polling-worker fault/recovery tests."""
from __future__ import annotations

import threading
from dataclasses import replace
from threading import Event, Thread, current_thread

import pytest

import backend.telemetry.service as implementation
from backend.telemetry import SignalUnavailable, TelemetryConfiguration, TelemetryMode, TelemetryService

SECOND = 1_000_000_000


class InjectedFailure(RuntimeError):
    """Identifies the exact injected platform failure without involving real hardware."""


class Clock:
    """Separates main-thread acquisition faults from event-controlled worker faults."""

    def __init__(self) -> None:
        """Starts at a fixed epoch with all one-shot fault points disabled."""
        self.now = 10 * SECOND
        self.mainRead = 0
        self.mainFailAt = None
        self.workerRead = 0
        self.workerFailAt = None
        self.failWall = False
        self.error = InjectedFailure("injected clock failure")

    def monotonic(self) -> int:
        """Raises once at the configured real acquisition or polling clock boundary."""
        if current_thread().name == "actant-telemetry":
            self.workerRead += 1
            if self.workerRead == self.workerFailAt:
                raise self.error
        else:
            self.mainRead += 1
            if self.mainRead == self.mainFailAt:
                raise self.error
        return self.now

    def wall(self) -> int:
        """Can fail inside actual background collection, distinct from deadline handling."""
        if self.failWall and current_thread().name == "actant-telemetry":
            self.failWall = False
            raise self.error
        return 500 * SECOND


class Gpu:
    """Tracks preparation/read/cleanup while ordinary sensor absence remains handled."""

    def __init__(self) -> None:
        """Creates a fake resource with observable reads and deterministic teardown."""
        self.reads = 0
        self.closes = 0
        self.collected = Event()

    def gpu(self) -> SignalUnavailable:
        """Signals a genuine collector visit and supplies explicit missing sensor evidence."""
        self.reads += 1
        self.collected.set()
        return SignalUnavailable("fake sensor absent")

    def close(self) -> None:
        """Counts service-owned teardown without touching native resources."""
        self.closes += 1


def configuration(seconds: float = 1000) -> TelemetryConfiguration:
    """Requests bounded GPU evidence with long deadlines controlled by fake time."""
    return TelemetryConfiguration(memoryMode=TelemetryMode.DISABLED, cpuMode=TelemetryMode.DISABLED,
                                  gpuMode=TelemetryMode.HISTORY, sampleIntervalSeconds=seconds, historySamples=2)


@pytest.fixture
def harness(monkeypatch):
    """Captures real thread failures and always joins every test-created worker."""
    clock, gpu = Clock(), Gpu()
    made = []
    workers = []
    errors = []
    failed = Event()

    def factory():
        """Creates a fresh service-owned fake GPU for each resource lifetime."""
        resource = gpu if not made else Gpu()
        made.append(resource)
        return resource

    def threadFactory(*args, **kwargs):
        """Uses real threads while retaining handles for bounded teardown joins."""
        worker = Thread(*args, **kwargs)
        workers.append(worker)
        return worker

    def exceptionHook(arguments):
        """Observes the unchanged Python thread exception channel deterministically."""
        errors.append(arguments.exc_value)
        failed.set()

    monkeypatch.setattr(implementation, "Thread", threadFactory)
    monkeypatch.setattr(threading, "excepthook", exceptionHook)
    service = TelemetryService(machineProvider=None, gpuProvider=None, gpuProviderFactory=factory,
                               configuration=configuration(), monotonicNs=clock.monotonic, wallTimeNs=clock.wall)
    yield service, clock, gpu, made, workers, errors, failed
    clock.mainFailAt = clock.workerFailAt = None
    clock.failWall = False
    for subscription in tuple(service._subscriptions):
        subscription.release()
    service.stop()
    for worker in workers:
        if worker.ident is not None:
            worker.join(5)
            assert not worker.is_alive(), "test leaked a polling worker"


@pytest.mark.parametrize("failAt", [1, 2])
@pytest.mark.parametrize("borrowed", [False, True])
def test_initial_collection_failure_rolls_back_and_reacquires(harness, failAt, borrowed):
    """Real clock failure before/after provider preparation cannot leave an unreachable claim."""
    service, clock, gpu, made, _workers, _errors, _failed = harness
    if borrowed:
        service._gpu, service._gpuAttempted = gpu, True
    clock.mainFailAt = failAt
    owner = service.owner()
    with pytest.raises(InjectedFailure) as raised:
        owner.subscribe(configuration())
    assert raised.value is clock.error
    assert not service._subscriptions
    assert owner._subscriptions == set()
    assert service.collectionIntervalSeconds is None
    assert service._latest is None
    assert gpu.closes == (int(failAt == 2) if not borrowed else 0)
    clock.mainFailAt = None
    with owner.subscribe(configuration()) as recovered:
        assert recovered.current is not None
        assert len(recovered.history) == 1
        assert isinstance(recovered.current.gpu, SignalUnavailable)
    owner.release()
    assert not service._subscriptions


@pytest.mark.parametrize("phase", ["construct", "start", "setup-after-start"])
@pytest.mark.parametrize("existing", [False, True])
def test_worker_setup_failure_revokes_only_new_claim(harness, monkeypatch, phase, existing):
    """Constructor/start/setup failures roll back acquisition and preserve existing history/resources."""
    service, clock, gpu, _made, workers, _errors, _failed = harness
    prior = None
    if existing:
        # A CURRENT legacy consumer has retained data/provider ownership but no poller.
        service.configuration = replace(configuration(), gpuMode=TelemetryMode.CURRENT)
        service.start()
        prior = service.current
    expectedClaims = set(service._subscriptions)
    realFactory, realEnsure = implementation.Thread, service._ensureWorker
    error = InjectedFailure("worker setup failed")
    def failStart():
        """Models Thread.start failure after the service publishes its worker reference."""
        raise error
    def faultyFactory(*args, **kwargs):
        """Fails construction or injects start failure into an otherwise real Thread."""
        if phase == "construct":
            raise error
        worker = realFactory(*args, **kwargs)
        worker.start = failStart
        return worker
    def failAfterStart():
        """Exercises rollback after the actual worker has started behind the lifecycle lock."""
        realEnsure()
        raise error
    with monkeypatch.context() as fault:
        if phase == "setup-after-start":
            fault.setattr(service, "_ensureWorker", failAfterStart)
        else:
            fault.setattr(implementation, "Thread", faultyFactory)
        with pytest.raises(InjectedFailure) as raised:
            service.subscribe(configuration(10))
        assert raised.value is error
    for worker in tuple(workers):
        if worker.ident is not None:
            worker.join(5)
            assert not worker.is_alive()
    assert service._subscriptions == expectedClaims
    assert service._thread is None
    assert service.collectionIntervalSeconds is None
    assert service.current is prior
    assert gpu.closes == (0 if existing else 1)
    if existing:
        assert service._gpu is gpu
    with service.subscribe(configuration()) as recovered:
        assert recovered.current is not None
        assert service._thread is not None
    if existing:
        assert service.current is prior
        service.stop()
        assert gpu.closes == 1


@pytest.mark.parametrize("fault", ["deadline", "collect-start", "collect-end", "wall", "wait"])
@pytest.mark.parametrize("recovery", ["subscribe", "sample"])
def test_unexpected_worker_failure_clears_identity_and_explicitly_recovers(harness, monkeypatch, fault, recovery):
    """A real failed polling thread reports its original error and permits one explicit replacement."""
    service, clock, gpu, _made, workers, errors, failed = harness
    owner = service.owner()
    first = owner.subscribe(configuration())
    old = service._thread
    original = first.current
    originalWait = service._condition.wait
    with service._condition:
        # Reset under the collector lock; whether the worker has first waited is irrelevant.
        clock.workerRead = 0
        clock.now += 2000 * SECOND
        clock.workerFailAt = {"deadline": 1, "collect-start": 2, "collect-end": 3}.get(fault)
        clock.failWall = fault == "wall"
        if fault == "wait":
            clock.now = original.collectionEndNs
            def failWait(*_args, **_kwargs):
                """Fails actual scheduler waiting before any new collection can occur."""
                raise clock.error
            monkeypatch.setattr(service._condition, "wait", failWait)
        service._condition.notify_all()
    assert failed.wait(5)
    old.join(5)
    assert not old.is_alive()
    assert errors == [clock.error]
    assert service._thread is None

    assert first.current is original and first.history == (original,)
    assert len(workers) == 1  # There is no autonomous retry loop.
    monkeypatch.setattr(service._condition, "wait", originalWait)
    with service._condition:
        clock.workerFailAt = None
        clock.now = original.collectionStartNs + 5000 * SECOND
        gpu.collected.clear()
        if recovery == "subscribe":
            second = service.subscribe(configuration(2000))
            assert second.current == original  # Reused evidence is never restamped as fresh.
        else:
            second = None
            service.sample()
            assert first.current.collectionStartNs == clock.now
        replacement = service._thread
        assert replacement is not None and replacement is not old
    if recovery == "subscribe":
        assert gpu.collected.wait(5)
    with service._condition:
        assert first.current.collectionStartNs == clock.now
        assert first.current.actualIntervalSeconds == 5000
        assert len(first.history) == 2
        assert len(workers) == 2
    owner.release()
    owner.release()
    if second is not None:
        assert not second.released
        second.release()
        second.release()
    replacement.join(5)
    assert not replacement.is_alive()
    assert service._thread is None


@pytest.mark.parametrize("reverse", [False, True])
def test_failed_join_preserves_live_subscribers_and_legacy_history(harness, monkeypatch, reverse):
    """A failed fast joining claim cannot reset existing consumers, histories or providers."""
    service, clock, gpu, _made, workers, _errors, _failed = harness
    service.start()
    owner = service.owner()
    first = owner.subscribe(configuration(2000))
    second = service.subscribe(configuration(3000))
    worker = service._thread
    legacyHistory, firstHistory, secondHistory = service.history, first.history, second.history
    realEnsure = service._ensureWorker
    def failSetup():
        """Fails joining setup after the real shared-worker negotiation path."""
        realEnsure()
        raise InjectedFailure("joining setup failed")
    with monkeypatch.context() as fault:
        fault.setattr(service, "_ensureWorker", failSetup)
        with pytest.raises(InjectedFailure, match="joining setup failed"):
            service.subscribe(configuration(1))
    assert service._subscriptions == {first, second}
    assert service._thread is worker and len(workers) == 1
    assert service.collectionIntervalSeconds == 1000
    assert service.history == legacyHistory and first.history == firstHistory and second.history == secondHistory
    assert gpu.closes == 0
    with service.subscribe(configuration(4000)) as recovered:
        assert recovered.current == first.current
    if reverse:
        service.stop()
        assert gpu.closes == 0
        owner.release()
        second.release()
    else:
        second.release()
        second.release()
        owner.release()
        assert gpu.closes == 0
        service.stop()
    worker.join(5)
    assert not worker.is_alive()
    assert gpu.closes == 1


@pytest.mark.parametrize("exceptionType", [InjectedFailure, KeyboardInterrupt])
def test_rollback_preserves_original_failure_even_if_cleanup_fails(harness, monkeypatch, exceptionType):
    """Partial evidence is cleared and teardown failure cannot replace acquisition evidence."""
    service, _clock, gpu, _made, _workers, _errors, _failed = harness
    captured = []
    realSubscription = implementation.TelemetrySubscription
    realCleanup = service._cleanup
    original = exceptionType("original acquisition failure")
    def subscriptionFactory(*args, **kwargs):
        """Retains the otherwise unreachable real claim for disposal assertions."""
        subscription = realSubscription(*args, **kwargs)
        captured.append(subscription)
        return subscription
    def failSetup():
        """Fails after actual collection has populated the pending claim."""
        raise original
    def failCleanup():
        """Completes ordinary disposal before simulating a second cleanup failure."""
        realCleanup()
        raise OSError("cleanup failure")
    with monkeypatch.context() as fault:
        fault.setattr(implementation, "TelemetrySubscription", subscriptionFactory)
        fault.setattr(service, "_ensureWorker", failSetup)
        fault.setattr(service, "_cleanup", failCleanup)
        with pytest.raises(exceptionType) as raised:
            service.subscribe(configuration())
    assert raised.value is original
    assert any("OSError" in note for note in raised.value.__notes__)
    assert captured[0].released and captured[0].current is None and captured[0].history == ()
    assert not service._subscriptions and service._latest is None
    assert gpu.closes == 1
    with service.subscribe(configuration()) as recovered:
        assert recovered.current is not None


def test_started_then_failed_start_cannot_collect_or_clear_replacement(harness, monkeypatch):
    """An old thread delayed before loop entry cannot affect a newer published worker."""
    service, _clock, gpu, _made, workers, _errors, _failed = harness
    enterOld = Event()
    realFactory = implementation.Thread
    def threadFactory(*args, **kwargs):
        """Starts a genuine old thread whose entry can be delayed across recovery."""
        originalTarget = kwargs.pop("target")
        def delayedEntry():
            """Allows a replacement to be published before the old polling loop enters."""
            assert enterOld.wait(5)
            originalTarget()
        worker = realFactory(*args, target=delayedEntry, **kwargs)
        realStart = worker.start
        def failAfterRealStart():
            """Models partial startup failure after a real OS thread exists."""
            realStart()
            raise InjectedFailure("startup failed after starting")
        worker.start = failAfterRealStart
        return worker
    try:
        with monkeypatch.context() as fault:
            fault.setattr(implementation, "Thread", threadFactory)
            with pytest.raises(InjectedFailure, match="startup failed after starting"):
                service.subscribe(configuration())
        assert service._thread is None and not service._subscriptions
        old = workers[0]
        with service.subscribe(configuration()) as recovered:
            replacement = service._thread
            assert replacement is not old
            reads = sum(resource.reads for resource in _made)
            enterOld.set()
            old.join(5)
            assert not old.is_alive()
            assert service._thread is replacement
            assert sum(resource.reads for resource in _made) == reads
            assert recovered.current is not None
        replacement.join(5)
        assert not replacement.is_alive()
    finally:
        enterOld.set()


@pytest.mark.parametrize("failAt", [1, 2])
def test_collection_failure_joining_surviving_disabled_consumer(harness, failAt):
    """A remaining disabled consumer survives first collection of a new hardware lifetime."""
    service, clock, _gpu, made, _workers, _errors, _failed = harness
    disabled = service.subscribe(replace(configuration(), gpuMode=TelemetryMode.DISABLED))
    temporary = service.subscribe(configuration())
    worker = service._thread
    service.sample()
    temporary.release()
    worker.join(5)
    assert not worker.is_alive()
    assert made[0].closes == 1
    prior = disabled.current
    assert service._latest is None
    clock.mainFailAt = clock.mainRead + failAt
    with pytest.raises(InjectedFailure):
        service.subscribe(configuration())
    assert service._subscriptions == {disabled}
    assert not disabled.released and disabled.current is prior
    assert service.collectionIntervalSeconds is None
    assert all(resource.closes == 1 for resource in made)
    clock.mainFailAt = None
    with service.subscribe(configuration()) as recovered:
        assert recovered.current is not None
    disabled.release()
    assert not service._subscriptions
