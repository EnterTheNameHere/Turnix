# file: tests/backend/application/test_observation.py ; version: 1
"""Architectural observation tests through real contexts, Jobs, runtimes and hosts."""
from __future__ import annotations

import gc
import json
import weakref
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from threading import Event, Thread

import pytest

from backend.application.applicationRuntime import ApplicationRuntime
from backend.context import CodeEntryIdentity
from backend.llm.llmTypes import LlmExecutionProfile, LlmQuery, LlmStreamEvent
from backend.llm.streamObservation import StreamObservationWindow, StreamOutcome
from backend.observation import (
    ObservationLifetime, ObservationRequest, ObservationService, correlate,
)
from backend.orchestration import JobState
from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.processing.runtime import QueryItem
from backend.registration import RegistrationScope
from backend.runtime import RuntimeHost
from backend.save import ApplicationStore
from backend.telemetry import SignalUnavailable, TelemetryConfiguration, TelemetryMode, TelemetryService, TelemetrySnapshot
from backend.values import MISSING

SECOND = 1_000_000_000
QUERY = LlmQuery(formatId="text/plain", payload="question")


class Clock:
    """One host monotonic domain plus independently jumping display wall time."""

    def __init__(self) -> None:
        """Starts at a nonzero monotonic epoch and records optional clock failures."""
        self.now = 10 * SECOND
        self.wall = 500 * SECOND
        self.fail = False

    def read(self) -> int:
        """Returns monotonic time or an injected optional measurement failure."""
        if self.fail:
            raise OSError("clock failure")
        return self.now

    def display(self) -> int:
        """Returns wall time without any relation to elapsed observation time."""
        return self.wall


class Hardware:
    """Counts physical reads and models a slow, failing optional GPU sensor."""

    def __init__(self, clock: Clock) -> None:
        """Binds sequential-read latency to the same host monotonic clock."""
        self.clock = clock
        self.calls = 0
        self.closes = 0

    def gpu(self):
        """Consumes time before reporting explicit sensor unavailability."""
        self.calls += 1
        self.clock.now += SECOND // 5
        raise OSError("no sensor")

    def close(self) -> None:
        """Records provider cleanup for independent-claim assertions."""
        self.closes += 1


class Provider:
    """Deterministic provider supporting normal, failed, cancelled and closed exits."""

    def __init__(self, clock: Clock, telemetry: TelemetryService, *, mode: str = "success") -> None:
        """Selects exit behavior while retaining engine-owned native usage evidence."""
        self.clock = clock
        self.telemetry = telemetry
        self.mode = mode
        self.observer = None

    def getExecutionProfile(self, **_kwargs):
        """Excludes simulated profile preparation from the stream origin."""
        self.clock.now += SECOND
        if self.mode == "prepare-fail":
            raise LookupError("profile failed")
        return LlmExecutionProfile(contextWindowTokens=4096)

    def stream(self, request):
        """Delivers Unicode text separated by long silent windows and hardware reads."""
        self.clock.now += SECOND // 4
        yield LlmStreamEvent(eventType="delta", text="é🙂")
        self.clock.now += 3 * SECOND
        self.clock.wall = -1000 * SECOND
        self.telemetry.sample()
        yield LlmStreamEvent(eventType="delta", text="abc")
        if self.mode == "fail":
            raise LookupError("provider failed")
        if self.mode == "cancel":
            request.cancellationSignal.request()
            raise OSError("transport stopped")
        if self.mode == "close":
            raise GeneratorExit("provider closed")
        yield LlmStreamEvent(eventType="completed", metadata={"usage": {"completion_tokens": 7}})


@pytest.fixture
def environment(monkeypatch):
    """Composes actual PM-01/02 with manual deterministic shared polling, no hardware."""
    def noWorker(_service):
        """Leaves polling advancement to explicit fake-provider sample points."""
    monkeypatch.setattr(TelemetryService, "_ensureWorker", noWorker)
    clock = Clock()
    hardware = Hardware(clock)
    telemetry = TelemetryService(machineProvider=None, gpuProvider=hardware, monotonicNs=clock.read, wallTimeNs=clock.display)
    service = ObservationService(telemetry, monotonicNs=clock.read)
    yield clock, hardware, telemetry, service
    service.close()
    telemetry.stop()


def runtimeWithContext(service: ObservationService):
    """Creates a real standalone runtime/context using injected host composition."""
    runtime = ApplicationRuntime(appPackId="test.app", packResolver=PackResolver(roots=()), observationService=service)
    runtime.start()
    identity = CodeEntryIdentity(
        applicationId=runtime.applicationRun.application.applicationId,
        applicationRunId=runtime.applicationRun.applicationRunId,
        packId="test.app", packVersion="1", codeEntryId="entry",
        codeEntryInstanceId="test-entry", sourceSha256="source",
        implementationFormat="python-source@1", implementationId="implementation",
    )
    runtime.registerCodeEntry(identity, Path.cwd())
    scope = RegistrationScope()
    ctx = runtime.createContext(identity=identity, packRoot=Path.cwd(), registrationScope=scope)
    return runtime, ctx, scope


def registerProvider(runtime, scope, provider):
    """Publishes the generic fake under the ordinary application provider registry."""
    runtime.llmProviders.register(scope, ownerId="test-entry", name="test", provider=provider)


def test_expired_invocation_independent_ownership_and_no_retained_context(environment):
    """Explicit ApplicationRun lifetime survives only its context, without retaining it."""
    _clock, hardware, telemetry, service = environment
    runtime, ctx, _scope = runtimeWithContext(service)
    session = ctx.observations.request(lifetime=ObservationLifetime.APPLICATION)
    invocation = ctx.observations.request()
    reference = weakref.ref(ctx)
    ctx.invalidate()
    with pytest.raises(RuntimeError, match="no longer valid"):
        ctx.observations.request(lifetime=ObservationLifetime.APPLICATION)
    with pytest.raises(RuntimeError, match="no longer valid"):
        ctx.llm.run(providerName="test", query=QUERY, observation=session)
    assert invocation.snapshot().released
    assert not session.snapshot().released
    del ctx
    gc.collect()
    assert reference() is None
    assert hardware.calls == 1
    assert telemetry.collectionIntervalSeconds == 1
    runtime.close()
    assert session.snapshot().released
    assert telemetry.collectionIntervalSeconds is None


def test_application_isolation_single_use_and_explicit_release(environment):
    """Sessions grant no foreign execution authority and release only their own claim."""
    clock, _hardware, telemetry, service = environment
    first, ctx, scope = runtimeWithContext(service)
    second, other, _ = runtimeWithContext(service)
    session = ctx.observations.request(lifetime=ObservationLifetime.APPLICATION)
    sibling = other.observations.request(lifetime=ObservationLifetime.APPLICATION)
    with pytest.raises(RuntimeError, match="foreign"):
        other.llm.run(providerName="test", query=QUERY, observation=session)
    registerProvider(first, scope, Provider(clock, telemetry))
    scope.publish()
    result = ctx.llm.run(providerName="test", query=QUERY, observation=session)
    assert result.providerMetadata["usage"]["completion_tokens"] == 7
    assert session.snapshot().stream.characterCount == 5
    with pytest.raises(RuntimeError, match="already used"):
        ctx.llm.run(providerName="test", query=QUERY, observation=session)
    saved = session.snapshot()
    session.release()
    session.release()
    assert session.snapshot().released and not session.snapshot().hardware
    assert saved.stream.characterCount == 5
    assert not sibling.snapshot().released
    first.close()
    assert telemetry.collectionIntervalSeconds is not None
    other.invalidate()
    second.stop()
    assert sibling.snapshot().released
    assert telemetry.collectionIntervalSeconds is None
    ctx.invalidate()
    second.close()


@pytest.mark.parametrize("mode", ["success", "fail", "cancel", "prepare-fail", "completion-fail"])
@pytest.mark.parametrize("monitoringFailure", [False, True])
def test_real_job_processing_cleanup_and_outcomes(environment, mode, monitoringFailure):
    """Actual capability contexts preserve Job/transaction outcomes on all ordinary exits."""
    clock, _hardware, telemetry, service = environment
    runtime, initial, scope = runtimeWithContext(service)
    initial.invalidate()
    provider = Provider(clock, telemetry, mode=mode)
    registerProvider(runtime, scope, provider)
    evidence = []
    sessions = []

    def items(ctx, _payload):
        """Stages a speculative processing mutation through real context authority."""
        tx = ctx.memory.openTransaction()
        tx.set("test/staged", "value")
        tx.commit()
        return [QueryItem(itemId="one", kind="test", content="question")]

    def query(_ctx, _payload):
        """Builds a provider-neutral query inside the ProcessingRun."""
        return QUERY

    def complete(_ctx, _payload):
        """Optionally fails after stream completion to distinguish Job and stream outcome."""
        if mode == "completion-fail":
            raise ValueError("completion failed")

    def handler(ctx, _payload):
        """Requests RUN monitoring and exercises the application-facing processing path."""
        session = ctx.observations.request()
        sessions.append(session)
        if monitoringFailure:
            session._stream._clock = brokenClock
        try:
            return ctx.llm.runProcessing(
                memoryKey="observation", inputValue={}, buildQueryItemsCapabilityId="items@1",
                buildQueryCapabilityId="query@1", completionCapabilityId="complete@1",
                providerName="test", observation=session,
            )
        finally:
            evidence.append(session.snapshot())
            assert telemetry.collectionIntervalSeconds is None

    def brokenClock():
        """Fails only optional stream measurement, leaving execution clocks intact."""
        raise OSError("optional stream clock")

    for name, handlerValue in (("items", items), ("query", query), ("complete", complete), ("job", handler)):
        runtime.capabilities.register(scope, ownerId="test-entry", capabilityId=name+"@1", handler=handlerValue)
    scope.publish()
    job = runtime.runJob("job@1")
    expected = JobState.SUCCEEDED if mode == "success" else JobState.CANCELLED if mode == "cancel" else JobState.FAILED
    assert job.state is expected
    assert job.authoritativeStateAccepted is (mode == "success")
    assert runtime.applicationRun.application.committedState.load("test/staged") == ("value" if mode == "success" else MISSING)
    assert sessions[0].snapshot().released
    assert not evidence[0].stream.active
    assert evidence[0].collectionStopped
    if mode == "prepare-fail":
        assert evidence[0].stream.originNs is None
    elif not monitoringFailure:
        outcome = StreamOutcome.CANCELLED if mode == "cancel" else StreamOutcome.FAILED if mode == "fail" else StreamOutcome.COMPLETED
        assert evidence[0].stream.outcome is outcome
        assert evidence[0].stream.characterCount == 5
    runtime.close()


def test_monotonic_correlation_delayed_samples_gaps_and_retention(environment):
    """Wall jumps, sequential reads, silence and bounded eviction remain visible."""
    clock, _hardware, telemetry, service = environment
    runtime, ctx, scope = runtimeWithContext(service)
    registerProvider(runtime, scope, Provider(clock, telemetry))
    scope.publish()
    session = ctx.observations.request(ObservationRequest(historySamples=2, historyWindows=2))
    result = ctx.llm.run(providerName="test", query=QUERY, observation=session)
    snapshot = session.snapshot()
    assert snapshot.stream.firstTextLatencyNs == SECOND // 4
    assert snapshot.stream.characterCount == len(result.rawText) == 5
    assert snapshot.stream.droppedWindowCount == 2
    assert len(snapshot.stream.windows) == 2
    assert snapshot.hardwareHistoryMayBeTruncated
    assert snapshot.hardware[0].sampledTimeNs > snapshot.hardware[1].sampledTimeNs
    assert snapshot.hardware[0].collectionStartNs < snapshot.hardware[1].collectionStartNs
    assert snapshot.hardware[1].collectionEndNs - snapshot.hardware[1].collectionStartNs == SECOND // 5
    assert snapshot.hardware[1].actualIntervalSeconds > 4
    assert snapshot.windows[0].hardware == ()
    assert snapshot.windows[0].uncoveredIntervals == ((snapshot.windows[0].stream.startNs, snapshot.windows[0].stream.endNs),)
    assert snapshot.windows[1].hardware == (snapshot.hardware[1],)
    assert snapshot.windows[1].uncoveredIntervals
    assert isinstance(snapshot.hardware[1].gpu, SignalUnavailable)
    with pytest.raises(FrozenInstanceError):
        snapshot.released = True
    session.release()
    assert snapshot.stream.characterCount == 5
    ctx.invalidate()
    runtime.close()


def test_correlation_unknown_timing_overlap_boundaries_and_no_interpolation():
    """Legacy timing stays unaligned; adjacent windows never duplicate endpoint samples."""
    unavailable = SignalUnavailable("absent")
    unknown = TelemetrySnapshot(500, unavailable, unavailable, unavailable, unavailable, unavailable)
    samples = (unknown, replace(unknown, collectionStartNs=2, collectionEndNs=7),
               replace(unknown, collectionStartNs=5, collectionEndNs=5),
               replace(unknown, collectionStartNs=4, collectionEndNs=3))
    rows = correlate((StreamObservationWindow(0, 5), StreamObservationWindow(5, 10)), samples)
    assert rows[0].hardware == (samples[1],)
    assert rows[0].uncoveredIntervals == ((0, 2),)
    assert rows[1].hardware == (samples[1], samples[2])
    assert rows[1].uncoveredIntervals == ((7, 10),)
    assert correlate((StreamObservationWindow(5, 5),), samples)[0].hardware == (samples[1], samples[2])


@pytest.mark.parametrize("mode", ["missing", "failing", "acquisition-failure"])
def test_missing_or_failing_hardware_keeps_stream_useful(environment, monkeypatch, mode):
    """Hardware absence/failure stays explicit and generic character rates still work."""
    clock, _hardware, telemetry, service = environment
    if mode == "missing":
        telemetry._gpu = None
    elif mode == "acquisition-failure":
        def failSubscribe(_configuration):
            """Models an unavailable optional host service before acquisition."""
            raise OSError("service unavailable")
        monkeypatch.setattr(telemetry, "subscribe", failSubscribe)
    runtime, ctx, scope = runtimeWithContext(service)
    registerProvider(runtime, scope, Provider(clock, telemetry))
    scope.publish()
    session = ctx.observations.request()
    ctx.llm.run(providerName="test", query=QUERY, observation=session)
    snap = session.snapshot()
    assert snap.stream.characterCount == 5
    if mode == "acquisition-failure":
        assert snap.hardwareUnavailableReason == "OSError" and snap.hardware == ()
    else:
        assert all(isinstance(sample.gpu, SignalUnavailable) for sample in snap.hardware)
    ctx.invalidate()
    runtime.close()


def test_disabled_path_and_all_disabled_request(environment, monkeypatch):
    """Ordinary execution allocates no observation handle or collection/history claim."""
    clock, _hardware, telemetry, service = environment
    runtime, ctx, scope = runtimeWithContext(service)
    provider = Provider(clock, telemetry)
    registerProvider(runtime, scope, provider)
    scope.publish()
    def noConstruction(*_args, **_kwargs):
        """Rejects any disabled-path stream allocation or subscription acquisition."""
        raise AssertionError("disabled path did monitoring work")
    monkeypatch.setattr("backend.observation.StreamObservation", noConstruction)
    monkeypatch.setattr(telemetry, "subscribe", noConstruction)
    assert ctx.llm.run(providerName="test", query=QUERY).rawText == "é🙂abc"
    session = ctx.observations.request(ObservationRequest(memory=False, cpu=False, gpu=False, stream=False))
    assert session.snapshot().hardware == () and session.snapshot().stream is None
    assert telemetry.collectionIntervalSeconds is None
    ctx.invalidate()
    runtime.close()


@pytest.mark.parametrize("field,value", [("memory", 1), ("stream", None), ("historySamples", 0), ("historyWindows", True), ("sampleIntervalSeconds", float("nan")), ("windowSeconds", 0)])
def test_provider_neutral_request_validation(field, value):
    """Invalid needs/cadence/retention fail before acquiring optional facilities."""
    with pytest.raises((ValueError, TypeError)):
        ObservationRequest(**{field: value})


def test_host_create_load_teardown_and_shared_collection(environment, tmp_path):
    """Actual host create/load paths share one collector and revoke only their scopes."""
    _clock, hardware, telemetry, _service = environment
    packs = tmp_path / "packs"
    packs.mkdir()
    (packs / "manifest.json").write_text(json.dumps({"packId": "test.app", "kind": "appPack", "version": "0.0.0", "codeEntries": []}), encoding="utf-8")
    host = RuntimeHost(applicationStore=ApplicationStore(tmp_path / "saves"), packResolver=PackResolver(roots=(packs,)), telemetryService=telemetry)
    host.start()
    plan = ManualActivationPlan(packIds=("test.app",))
    first = host.createApplication(appPackId="test.app", plan=plan)
    second = host.createApplication(appPackId="test.app", plan=plan)
    # Host-controlled fixtures create contexts with real per-runtime owner wiring.
    def context(runtime):
        """Builds an ordinary invocation context for the hosted runtime identity."""
        identity = CodeEntryIdentity(runtime.applicationRun.application.applicationId, runtime.applicationRun.applicationRunId,
                                     "test.app", "1", "entry", "entry", "source", "python-source@1", "implementation")
        return runtime.createContext(identity=identity, packRoot=packs, registrationScope=RegistrationScope())
    a, b = context(first), context(second)
    fast = a.observations.request(ObservationRequest(sampleIntervalSeconds=1), lifetime=ObservationLifetime.APPLICATION)
    slow = b.observations.request(ObservationRequest(sampleIntervalSeconds=2), lifetime=ObservationLifetime.APPLICATION)
    assert hardware.calls == 1
    assert telemetry.collectionIntervalSeconds == 1
    external = telemetry.subscribe(TelemetryConfiguration(memoryMode=TelemetryMode.DISABLED, cpuMode=TelemetryMode.DISABLED, gpuMode=TelemetryMode.HISTORY, sampleIntervalSeconds=5))
    assert hardware.calls == 1
    bundle = first.saveApplication()
    a.invalidate()
    b.invalidate()
    host.closeApplicationRun(first.applicationRun.applicationRunId)
    assert fast.snapshot().released and not slow.snapshot().released
    assert telemetry.collectionIntervalSeconds == 2
    loaded = host.loadApplication(appPackId="test.app", applicationId=bundle.applicationId, plan=plan)
    c = context(loaded)
    afterLoad = c.observations.request(lifetime=ObservationLifetime.APPLICATION)
    c.invalidate()
    host.stop()
    assert slow.snapshot().released and afterLoad.snapshot().released
    assert not external.released and telemetry.collectionIntervalSeconds == 5
    assert hardware.closes == 0
    external.release()
    assert telemetry.collectionIntervalSeconds is None


@pytest.mark.parametrize("phase", ["create", "load"])
def test_host_failed_initialization_revokes_application_observations(environment, tmp_path, monkeypatch, phase):
    """Lifecycle failure after acquiring a session cannot leak an ApplicationRun claim."""
    _clock, _hardware, telemetry, _service = environment
    packs = tmp_path / "packs"
    packs.mkdir()
    (packs / "manifest.json").write_text(json.dumps({"packId": "test.app", "kind": "appPack", "version": "0.0.0", "codeEntries": []}), encoding="utf-8")
    host = RuntimeHost(applicationStore=ApplicationStore(tmp_path / "saves"), packResolver=PackResolver(roots=(packs,)), telemetryService=telemetry)
    host.start()
    plan = ManualActivationPlan(packIds=("test.app",))
    appId = None
    if phase == "load":
        runtime = host.createApplication(appPackId="test.app", plan=plan)
        appId = runtime.saveApplication().applicationId
        host.closeApplicationRun(runtime.applicationRun.applicationRunId)
    sessions = []
    def failLifecycle(*, runtime, plan):
        """Emulates a lifecycle hook failure after independently owned acquisition."""
        sessions.append(runtime._observationOwner.request(ObservationRequest(), ObservationLifetime.APPLICATION))
        raise ValueError("initialization failed")
    monkeypatch.setattr(f"backend.runtime.runtimeHost.ApplicationLifecycle.{phase}", failLifecycle)
    with pytest.raises(ValueError, match="initialization failed"):
        if phase == "create":
            host.createApplication(appPackId="test.app", plan=plan)
        else:
            host.loadApplication(appPackId="test.app", applicationId=appId, plan=plan)
    assert sessions[0].snapshot().released
    assert telemetry.collectionIntervalSeconds is None
    host.stop()


@pytest.mark.parametrize("mode", ["success", "fail", "close", "observer-fail"])
def test_direct_execution_all_exits_stop_run_collection(environment, mode):
    """The application direct-call seam cleans up even on consumer/control-flow exits."""
    clock, _hardware, telemetry, service = environment
    runtime, ctx, scope = runtimeWithContext(service)
    registerProvider(runtime, scope, Provider(clock, telemetry, mode=mode))
    scope.publish()
    session = ctx.observations.request()
    def observer(_event):
        """Models the existing consumer observer failing without monitoring interference."""
        if mode == "observer-fail":
            raise ValueError("consumer failed")
    try:
        if mode in {"success", "observer-fail"}:
            result = ctx.llm.run(providerName="test", query=QUERY, observation=session, streamObserver=observer)
            assert result.rawText == "é🙂abc"
            assert bool(result.observerErrors) is (mode == "observer-fail")
        else:
            expected = GeneratorExit if mode == "close" else ValueError if mode == "observer-fail" else LookupError
            with pytest.raises(expected):
                ctx.llm.run(providerName="test", query=QUERY, observation=session, streamObserver=observer)
        snapshot = session.snapshot()
        assert not snapshot.stream.active
        assert snapshot.collectionStopped
        assert telemetry.collectionIntervalSeconds is None
    finally:
        ctx.invalidate()
        runtime.close()


def test_revocation_during_real_job_does_not_cancel_or_change_outcome(environment):
    """Explicit independent release during provider iteration stops evidence only."""
    clock, _hardware, telemetry, service = environment
    runtime, initial, scope = runtimeWithContext(service)
    initial.invalidate()
    entered, resume = Event(), Event()
    sessions = []
    jobs = []
    class BlockingProvider(Provider):
        """Yields text then blocks until independent session disposal is complete."""

        def stream(self, request):
            """Keeps the Job running while another thread releases its observation."""
            yield LlmStreamEvent(eventType="delta", text="first")
            entered.set()
            assert resume.wait(5)
            assert not request.cancellationSignal.requested
            yield LlmStreamEvent(eventType="delta", text="last")
            yield LlmStreamEvent(eventType="completed")
    registerProvider(runtime, scope, BlockingProvider(clock, telemetry))
    def handler(ctx, _payload):
        """Exercises actual Job-owned cancellation and invocation lifetime wiring."""
        session = ctx.observations.request(lifetime=ObservationLifetime.APPLICATION)
        sessions.append(session)
        return ctx.llm.run(providerName="test", query=QUERY, observation=session).rawText
    runtime.capabilities.register(scope, ownerId="test-entry", capabilityId="blocking@1", handler=handler)
    scope.publish()
    def runJob():
        """Runs the real synchronous application spine on a deterministic worker."""
        jobs.append(runtime.runJob("blocking@1"))
    worker = Thread(target=runJob)
    try:
        worker.start()
        assert entered.wait(5)
        sessions[0].release()
        assert telemetry.collectionIntervalSeconds is None
        resume.set()
        worker.join(5)
        assert not worker.is_alive()
        assert jobs[0].state is JobState.SUCCEEDED
        assert sessions[0].snapshot().released
    finally:
        resume.set()
        worker.join(5)
        runtime.close()


def test_mixed_cadence_shared_provider_and_actual_hardware_eviction(environment):
    """Different bounded requests downsample one collector and expose possible truncation."""
    clock, hardware, telemetry, service = environment
    first, a, _ = runtimeWithContext(service)
    second, b, _ = runtimeWithContext(service)
    fast = a.observations.request(ObservationRequest(sampleIntervalSeconds=1, historySamples=2), lifetime=ObservationLifetime.APPLICATION)
    slow = b.observations.request(ObservationRequest(sampleIntervalSeconds=3, historySamples=3), lifetime=ObservationLifetime.APPLICATION)
    initial = fast.snapshot()
    assert hardware.calls == 1
    for _index in range(9):
        clock.now += SECOND
        telemetry.sample()
    assert hardware.calls == 10
    recent, downsampled = fast.snapshot(), slow.snapshot()
    assert len(recent.hardware) == 2 and len(downsampled.hardware) == 3
    assert recent.hardwareHistoryMayBeTruncated and downsampled.hardwareHistoryMayBeTruncated
    assert initial.hardware[0].collectionStartNs < recent.hardware[0].collectionStartNs
    assert all(y.collectionStartNs - x.collectionStartNs >= 3 * SECOND for x, y in zip(downsampled.hardware, downsampled.hardware[1:]))
    first.close()
    assert telemetry.collectionIntervalSeconds == 3
    assert not slow.snapshot().released
    a.invalidate()
    b.invalidate()
    second.close()


@pytest.mark.parametrize("failure", ["construct", "snapshot", "release"])
def test_optional_stream_facility_failure_preserves_job_outcome(environment, monkeypatch, failure):
    """Ordinary optional construction/read/disposal failures cannot turn success into failure."""
    clock, _hardware, telemetry, service = environment
    runtime, initial, scope = runtimeWithContext(service)
    initial.invalidate()
    registerProvider(runtime, scope, Provider(clock, telemetry))
    evidence = []
    def fail(*_args, **_kwargs):
        """Models a failing optional boundary without failing ordinary execution."""
        raise OSError("optional failure")
    def handler(ctx, _payload):
        """Runs inference and reads optional evidence before context disposal."""
        session = ctx.observations.request()
        if failure != "construct":
            monkeypatch.setattr(session._stream, failure, fail)
        result = ctx.llm.run(providerName="test", query=QUERY, observation=session)
        if failure == "release":
            session.release()
        evidence.append(session.snapshot())
        return result.rawText
    if failure == "construct":
        monkeypatch.setattr("backend.observation.StreamObservation", fail)
    runtime.capabilities.register(scope, ownerId="test-entry", capabilityId="optional@1", handler=handler)
    scope.publish()
    job = runtime.runJob("optional@1")
    assert job.state is JobState.SUCCEEDED
    assert evidence[0].streamUnavailableReason == "OSError"
    assert telemetry.collectionIntervalSeconds is None
    runtime.close()


def test_runtime_cleanup_failure_still_revokes_owner(environment, monkeypatch):
    """Application owner is revoked even when Pack teardown raises an aggregated error."""
    _clock, _hardware, telemetry, service = environment
    runtime, ctx, _ = runtimeWithContext(service)
    session = ctx.observations.request(lifetime=ObservationLifetime.APPLICATION)
    def failClose():
        """Models arbitrary Pack unload failure after owner revocation."""
        raise ValueError("pack close failed")
    monkeypatch.setattr(runtime.packLoader, "close", failClose)
    with pytest.raises(ExceptionGroup):
        runtime.close()
    assert session.snapshot().released
    with pytest.raises(RuntimeError, match="owner is released"):
        ctx.observations.request(lifetime=ObservationLifetime.APPLICATION)
    assert telemetry.collectionIntervalSeconds is None
    ctx.invalidate()


@pytest.mark.parametrize("mode", ["success", "fail", "cancel"])
def test_disabled_monitoring_preserves_real_job_outcomes(environment, monkeypatch, mode):
    """Jobs run with unchanged results and no optional acquisition when observation is absent."""
    clock, _hardware, telemetry, service = environment
    runtime, initial, scope = runtimeWithContext(service)
    initial.invalidate()
    registerProvider(runtime, scope, Provider(clock, telemetry, mode=mode))
    def noMonitoring(*_args, **_kwargs):
        """Fails any attempt to allocate optional measurement or acquire collection."""
        raise AssertionError("disabled monitoring was activated")
    monkeypatch.setattr("backend.observation.StreamObservation", noMonitoring)
    monkeypatch.setattr(telemetry, "subscribe", noMonitoring)
    def handler(ctx, _payload):
        """Executes normal provider work through the unobserved application facade."""
        return ctx.llm.run(providerName="test", query=QUERY).rawText
    runtime.capabilities.register(scope, ownerId="test-entry", capabilityId="disabled@1", handler=handler)
    scope.publish()
    try:
        job = runtime.runJob("disabled@1")
        expected = JobState.SUCCEEDED if mode == "success" else JobState.CANCELLED if mode == "cancel" else JobState.FAILED
        assert job.state is expected
        assert not telemetry._subscriptions
        assert telemetry.collectionIntervalSeconds is None
    finally:
        runtime.close()
