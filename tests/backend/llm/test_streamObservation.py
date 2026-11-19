# file: tests/backend/llm/test_streamObservation.py ; version: 1
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict
from threading import Event
from types import SimpleNamespace

import pytest

from backend.llm.errors import LlmProviderProtocolError
from backend.llm.llmTypes import LlmExecutionProfile, LlmQuery, LlmStreamEvent
from backend.llm.streamObservation import (
    StreamObservation,
    StreamObservationConfiguration,
    StreamObservationClaim,
    StreamOutcome,
)
from backend.llm.streamingRuntime import LlmProcessingPipeline, LlmProviderRegistry
from backend.orchestration import CancellationSignal, ExecutionCancelled
from backend.processing.runtime import QueryItem
from backend.registration import RegistrationScope
from backend.values import MISSING, CommittedValueLayer

SECOND = 1_000_000_000


class Clock:
    """Supplies deterministic monotonic time and precise optional-clock failure injection."""

    def __init__(self) -> None:
        """Starts apart from zero and counts every monitoring-specific clock read."""
        self.now = 10 * SECOND
        self.calls = 0
        self.failAt: int | None = None

    def read(self) -> int:
        """Returns time or raises at the configured observation lifecycle step."""
        self.calls += 1
        if self.calls == self.failAt:
            raise OSError("optional clock unavailable")
        return self.now


class Provider:
    """Delivers scheduled text and native metadata without hardware or external servers."""

    def __init__(self, clock: Clock, *, mode: str = "success") -> None:
        """Configures deterministic arrivals and stream termination behavior."""
        self.clock = clock
        self.mode = mode
        self.events = [(SECOND // 4, "answer")]
        self.endOffset = SECOND
        self.preparationNs = 0
        self.entered = False

    def getExecutionProfile(self, *, model: object, providerOptions: object) -> LlmExecutionProfile:
        """Simulates preparation that must be excluded from first-text latency."""
        self.clock.now += self.preparationNs
        return LlmExecutionProfile(contextWindowTokens=4096)

    def stream(self, request: object):
        """Delivers scheduled events, then completes, fails, closes or requests cancellation."""
        self.entered = True
        origin = self.clock.now
        for offset, text in self.events:
            self.clock.now = origin + offset
            yield LlmStreamEvent(eventType="delta", text=text)
        self.clock.now = origin + self.endOffset
        if self.mode == "fail":
            raise LookupError("provider failure")
        if self.mode == "close":
            raise GeneratorExit("provider closed early")
        if self.mode == "incomplete":
            return
        if self.mode == "cancel":
            request.cancellationSignal.request()
            raise OSError("transport interrupted")
        yield LlmStreamEvent(eventType="completed", metadata={"usage": {"completion_tokens": 7}})
        if self.mode == "late":
            yield LlmStreamEvent(eventType="delta", text="invalid late text")


def pipeline(provider: Provider, **kwargs: object) -> LlmProcessingPipeline:
    """Publishes a fake provider under the ordinary generic pipeline boundary."""
    providers = LlmProviderRegistry()
    scope = RegistrationScope()
    providers.register(scope, ownerId="observation-test", name="test", provider=provider)
    scope.publish()
    return LlmProcessingPipeline(providers=providers, **kwargs)


def run(provider: Provider, observation: StreamObservation | None = None, **kwargs: object):
    """Executes a direct call with optional observation through the public seam."""
    return pipeline(provider).run(
        providerName="test", query=LlmQuery(formatId="text/plain", payload="question"),
        streamObservation=observation, **kwargs,
    )


def test_origin_unicode_bursts_boundaries_gaps_and_separate_native_tokens() -> None:
    """Delivery windows preserve Unicode counts and timing without inventing native token rates."""
    clock = Clock()
    provider = Provider(clock)
    provider.preparationNs = 5 * SECOND
    provider.events = [
        (0, ""), (SECOND // 4, "a"), (SECOND // 4, "é🙂e\u0301"),
        (SECOND, "xy"), (3 * SECOND + SECOND // 5, "z"),
    ]
    provider.endOffset = 4 * SECOND
    observation = StreamObservation(monotonicNs=clock.read)
    assert clock.calls == 0
    result = run(provider, observation)
    snapshot = observation.snapshot()
    assert snapshot.originNs == 15 * SECOND
    assert snapshot.firstTextLatencyNs == SECOND // 4
    assert snapshot.characterCount == 8 and snapshot.deliveryCount == 4
    assert clock.calls == 6  # Begin, four non-empty deliveries, finish; no clock for empty/completed events.
    assert [window.characterCount for window in snapshot.windows] == [5, 2, 0, 1]
    assert [window.charactersPerSecond for window in snapshot.windows] == [5, 2, 0, 1]
    assert snapshot.windows[0].minGapNs == snapshot.windows[0].maxGapNs == 0
    assert snapshot.windows[1].meanGapNs == 3 * SECOND / 4
    assert snapshot.windows[3].maxGapNs == 2 * SECOND + SECOND // 5
    assert snapshot.silenceNs == 4 * SECOND / 5
    assert snapshot.endNs == snapshot.observedThroughNs == 19 * SECOND
    assert snapshot.outcome is StreamOutcome.COMPLETED and not snapshot.active
    assert result.rawText == "aé🙂e\u0301xyz"
    assert result.providerMetadata["usage"]["completion_tokens"] == 7
    assert not hasattr(snapshot, "tokensPerSecond")
    assert snapshot.droppedWindowCount == 0


@pytest.mark.parametrize("endOffset,rate", [(0, None), (SECOND // 2, 8.0)])
def test_partial_and_zero_duration_windows(endOffset: int, rate: float | None) -> None:
    """Partial throughput divides by elapsed time, with no rate for zero-duration bursts."""
    clock = Clock()
    provider = Provider(clock)
    provider.events = [(0, "four")]
    provider.endOffset = endOffset
    observation = StreamObservation(monotonicNs=clock.read)
    run(provider, observation)
    window = observation.snapshot().windows[-1]
    assert window.charactersPerSecond == rate and window.partial


def test_live_silence_and_huge_gap_have_bounded_retention() -> None:
    """Live reads show explicit zero-delivery windows with O(retention) gap advancement."""
    clock = Clock()
    observation = StreamObservation(StreamObservationConfiguration(historyWindows=3), monotonicNs=clock.read)
    claim = observation.begin()
    claim.recordText("x")
    clock.now += 10**24
    snapshot = observation.snapshot()
    assert len(snapshot.windows) == 3
    assert len(observation._windows) <= 3
    assert all(window.characterCount == 0 and window.charactersPerSecond == 0 for window in snapshot.windows)
    assert snapshot.characterCount == 1
    assert snapshot.droppedWindowCount == 10**15 - 3
    assert snapshot.silenceNs == 10**24
    claim.finish(StreamOutcome.COMPLETED)
    assert not observation.snapshot().active


def test_snapshots_are_immutable_and_observation_never_retains_content() -> None:
    """The observation retains scalar statistics rather than generated text or event objects."""
    clock = Clock()
    provider = Provider(clock)
    secret = "uniquely-sensitive-generated-content"
    provider.events = [(SECOND // 4, secret)]
    observation = StreamObservation(monotonicNs=clock.read)
    run(provider, observation)
    snapshot = observation.snapshot()
    assert secret not in repr(asdict(snapshot))
    assert secret not in repr(vars(observation))
    with pytest.raises(FrozenInstanceError):
        snapshot.characterCount = 0
    with pytest.raises(FrozenInstanceError):
        snapshot.windows[0].characterCount = 0
    observation.release()
    assert snapshot.characterCount == len(secret)
    assert observation.snapshot().windows == ()


def test_disabled_path_has_no_monitoring_allocation_clock_count_or_observer(monkeypatch) -> None:
    """Disabled calls keep only the original two execution clocks and original observer delivery."""
    calls = {"wall": 0, "monotonic": 0}

    def wall() -> int:
        """Counts pre-existing execution wall-clock endpoints."""
        calls["wall"] += 1
        return calls["wall"] * SECOND

    def monotonic() -> int:
        """Counts pre-existing execution monotonic endpoints."""
        calls["monotonic"] += 1
        return calls["monotonic"] * SECOND

    def forbidden(*_args: object, **_kwargs: object) -> None:
        """Fails if disabled execution tries to allocate, acquire, clock or count observations."""
        raise AssertionError("monitoring work on disabled path")

    monkeypatch.setattr("backend.llm.streamingRuntime.time", SimpleNamespace(time_ns=wall, monotonic_ns=monotonic))
    monkeypatch.setattr(StreamObservation, "__init__", forbidden)
    monkeypatch.setattr(StreamObservation, "begin", forbidden)
    monkeypatch.setattr(StreamObservation, "_now", forbidden)
    monkeypatch.setattr("backend.llm.streamObservation.len", forbidden, raising=False)
    monkeypatch.setattr(StreamObservationClaim, "recordText", forbidden)
    observed = []
    result = run(Provider(Clock()), streamObserver=observed.append)
    assert result.rawText == "answer" and result.observerErrors == ()
    assert len(observed) == 2
    assert calls == {"wall": 2, "monotonic": 2}


@pytest.mark.parametrize("failAt", [1, 2, 3])
def test_optional_clock_failure_at_every_lifecycle_stage_preserves_execution(failAt: int) -> None:
    """Acquire, delivery and final-clock failures all release the claim without changing provider success."""
    clock = Clock()
    clock.failAt = failAt
    observation = StreamObservation(monotonicNs=clock.read)
    observed = []
    result = run(Provider(clock), observation, streamObserver=observed.append)
    snapshot = observation.snapshot()
    assert result.rawText == "answer" and len(observed) == 2 and result.observerErrors == ()
    assert snapshot.unavailableReason == "OSError"
    assert snapshot.outcome is StreamOutcome.COMPLETED and not snapshot.active


def test_unexpected_recording_failure_preserves_existing_observer_semantics(monkeypatch) -> None:
    """An optional seam failure cannot suppress or duplicate the existing observer callback."""
    clock = Clock()
    observation = StreamObservation(monotonicNs=clock.read)
    seen = []

    def brokenRecord(_claim: object, _text: str) -> None:
        """Simulates an unexpected optional collector implementation failure."""
        raise RuntimeError("must not affect delivery")

    def observer(event: LlmStreamEvent) -> None:
        """Records ordinary events and preserves existing isolated callback-failure evidence."""
        seen.append(event.eventType)
        raise ValueError("existing observer failure")

    monkeypatch.setattr(StreamObservationClaim, "recordText", brokenRecord)
    result = run(Provider(clock), observation, streamObserver=observer)
    assert result.rawText == "answer"
    assert seen == ["delta", "completed"]
    assert result.observerErrors == ("ValueError: existing observer failure",) * 2
    snapshot = observation.snapshot()
    assert snapshot.unavailableReason == "RuntimeError" and not snapshot.active


@pytest.mark.parametrize(
    "mode,error,outcome",
    [
        ("success", None, StreamOutcome.COMPLETED),
        ("fail", LookupError, StreamOutcome.FAILED),
        ("incomplete", LlmProviderProtocolError, StreamOutcome.FAILED),
        ("late", LlmProviderProtocolError, StreamOutcome.FAILED),
        ("cancel", ExecutionCancelled, StreamOutcome.CANCELLED),
        ("close", GeneratorExit, StreamOutcome.CLOSED),
    ],
)
def test_every_stream_exit_releases_claim(mode: str, error: type | None, outcome: StreamOutcome) -> None:
    """Success, provider/protocol failure, cancellation and early close have distinct terminal evidence."""
    clock = Clock()
    observation = StreamObservation(monotonicNs=clock.read)
    provider = Provider(clock, mode=mode)
    if error is None:
        run(provider, observation)
    else:
        with pytest.raises(error):
            run(provider, observation, cancellationSignal=CancellationSignal())
    snapshot = observation.snapshot()
    assert not snapshot.active and snapshot.outcome is outcome
    assert snapshot.characterCount == len("answer")
    assert snapshot.endNs is not None


def test_early_consumer_close_releases_claim_and_propagates_control_flow() -> None:
    """A consumer's early GeneratorExit is not swallowed or relabeled as provider success."""
    clock = Clock()
    observation = StreamObservation(monotonicNs=clock.read)

    def observer(_event: LlmStreamEvent) -> None:
        """Stops delivery through the existing synchronous observer boundary."""
        raise GeneratorExit("consumer closed")

    with pytest.raises(GeneratorExit):
        run(Provider(clock), observation, streamObserver=observer)
    assert observation.snapshot().outcome is StreamOutcome.CLOSED
    assert not observation.snapshot().active


def test_explicit_release_during_delivery_does_not_stop_inference_and_is_idempotent() -> None:
    """Revoking observation disposes its data and makes later run callbacks inert."""
    clock = Clock()
    observation = StreamObservation(monotonicNs=clock.read)

    def observer(_event: LlmStreamEvent) -> None:
        """Revokes only optional observation while the provider continues normally."""
        observation.release()
        observation.release()

    assert run(Provider(clock), observation, streamObserver=observer).rawText == "answer"
    snapshot = observation.snapshot()
    assert snapshot.released and not snapshot.active
    assert snapshot.windows == () and snapshot.characterCount == 0
    assert observation.begin() is None


def test_observation_point_precedes_existing_callback_delay() -> None:
    """First-text time is captured before callback work, while later silence includes that work."""
    clock = Clock()
    observation = StreamObservation(monotonicNs=clock.read)

    def observer(event: LlmStreamEvent) -> None:
        """Inspects already-recorded delivery then consumes additional host time."""
        if event.eventType == "delta":
            assert observation.snapshot().firstTextNs == clock.now
            clock.now += SECOND // 2

    run(Provider(clock), observation, streamObserver=observer)
    assert observation.snapshot().firstTextLatencyNs == SECOND // 4


def processingPipeline(provider: Provider, state: CommittedValueLayer, *, failCompletion: bool = False):
    """Creates transactional processing without wiring any application authority facade."""
    def invoke(capabilityId: str, _payload: object, memoryView: object) -> object:
        """Stages ordinary state so tests can verify commit/rollback independently of observation."""
        if capabilityId == "items":
            memoryView.set("test/staged", "value")
            return [QueryItem(itemId="one", kind="test", content="question")]
        if capabilityId == "query":
            return LlmQuery(formatId="text/plain", payload="question")
        if capabilityId == "complete":
            if failCompletion:
                raise ValueError("completion failed after successful stream")
            return None
        raise AssertionError(capabilityId)

    return pipeline(provider, state=state, capabilityInvoker=invoke)


@pytest.mark.parametrize("mode", ["success", "fail", "cancel", "completion-failure"])
def test_monitoring_failure_preserves_processing_commit_and_rollback(mode: str) -> None:
    """Unavailable monitoring cannot change transactional success, failure or cancellation classification."""
    clock = Clock()
    clock.failAt = 2
    observation = StreamObservation(monotonicNs=clock.read)
    state = CommittedValueLayer()
    provider = Provider(clock, mode="success" if mode == "completion-failure" else mode)
    processing = processingPipeline(provider, state, failCompletion=mode == "completion-failure")
    arguments = dict(
        memoryKey="observation", inputValue={}, buildQueryItemsCapabilityId="items",
        buildQueryCapabilityId="query", completionCapabilityId="complete", providerName="test",
        cancellationSignal=CancellationSignal(), streamObservation=observation,
    )
    if mode == "success":
        result = processing.runProcessing(**arguments)
        assert state.load("test/staged") == "value"
        record = state.load(f"processing/observation/runs/{result.processingRunId}")
        assert "streamObservation" not in record
    else:
        error = ExecutionCancelled if mode == "cancel" else ValueError if mode == "completion-failure" else LookupError
        with pytest.raises(error):
            processing.runProcessing(**arguments)
        assert state.load("test/staged") is MISSING
        assert state.load("processing/observation/currentqueryitems") is MISSING
    expected = StreamOutcome.CANCELLED if mode == "cancel" else StreamOutcome.FAILED if mode == "fail" else StreamOutcome.COMPLETED
    snapshot = observation.snapshot()
    assert snapshot.outcome is expected and not snapshot.active
    assert snapshot.unavailableReason == "OSError"


def test_pre_stream_failure_acquires_no_claim_and_single_run_handle_cannot_be_reused() -> None:
    """Pre-cancelled calls do no monitoring work, and completed handles never mix another run's data."""
    clock = Clock()
    observation = StreamObservation(monotonicNs=clock.read)
    signal = CancellationSignal()
    signal.request()
    provider = Provider(clock)
    with pytest.raises(ExecutionCancelled):
        run(provider, observation, cancellationSignal=signal)
    assert clock.calls == 0 and not provider.entered
    assert observation.snapshot().originNs is None
    run(provider, observation)
    first = observation.snapshot()
    run(provider, observation)
    assert observation.snapshot() == first


def test_clock_regression_is_explicit_unavailability_not_negative_latency() -> None:
    """A broken monotonic source disables observation without modifying inference."""
    clock = Clock()
    observation = StreamObservation(monotonicNs=clock.read)
    provider = Provider(clock)
    provider.events = [(-SECOND, "text")]
    assert run(provider, observation).rawText == "text"
    snapshot = observation.snapshot()
    assert snapshot.unavailableReason == "ValueError" and snapshot.firstTextLatencyNs is None


def test_empty_only_stream_has_no_first_text_and_no_delivery_clock_work() -> None:
    """Silence remains a zero-delivery observation rather than a fabricated first-text time."""
    clock = Clock()
    provider = Provider(clock)
    provider.events = [(0, ""), (SECOND // 2, "")]
    observation = StreamObservation(monotonicNs=clock.read)
    run(provider, observation)
    snapshot = observation.snapshot()
    assert clock.calls == 2
    assert snapshot.firstTextLatencyNs is None and snapshot.deliveryCount == 0
    assert snapshot.windows[0].charactersPerSecond == 0
    assert snapshot.silenceNs == SECOND


def test_window_variation_reports_distinct_inter_delivery_spacings() -> None:
    """Per-window min/max/mean distinguish jitter from uniform delivery without storing events."""
    clock = Clock()
    provider = Provider(clock)
    provider.events = [(SECOND // 10, "a"), (SECOND // 5, "b"), (3 * SECOND // 5, "c")]
    observation = StreamObservation(monotonicNs=clock.read)
    run(provider, observation)
    window = observation.snapshot().windows[0]
    assert window.minGapNs == SECOND // 10
    assert window.maxGapNs == 2 * SECOND // 5
    assert window.meanGapNs == SECOND / 4


def test_finalization_failure_still_releases_and_preserves_original_provider_error(monkeypatch) -> None:
    """Even an internal finalization failure cannot mask failure or leave an active claim."""
    clock = Clock()
    observation = StreamObservation(monotonicNs=clock.read)

    def failAdvance(_now: int) -> None:
        """Simulates an internal measurement failure at delivery and finalization."""
        raise ValueError("measurement failed")

    provider = Provider(clock, mode="fail")
    provider.events = []  # Exercise failing finalization without an earlier recording failure.
    monkeypatch.setattr(observation, "_advance", failAdvance)
    with pytest.raises(LookupError, match="provider failure"):
        run(provider, observation)
    snapshot = observation.snapshot()
    assert snapshot.unavailableReason == "ValueError" and not snapshot.active
    assert snapshot.outcome is StreamOutcome.FAILED


def test_concurrent_release_serializes_with_recording_and_stops_further_measurement() -> None:
    """Revocation waits for an in-flight record and prevents later clocks/counts from that claim."""
    clock = Clock()
    observation = StreamObservation(monotonicNs=clock.read)
    claim = observation.begin()
    entered, resume, releaseStarted = Event(), Event(), Event()

    def blockingClock() -> int:
        """Holds the recording lane until the test permits the current delivery to finish."""
        entered.set()
        assert resume.wait(5)
        return clock.now

    def release() -> None:
        """Attempts independent revocation while one recording call owns the lane."""
        releaseStarted.set()
        observation.release()

    observation._clock = blockingClock
    with ThreadPoolExecutor(max_workers=2) as executor:
        recording = executor.submit(claim.recordText, "text")
        assert entered.wait(5)
        releasing = executor.submit(release)
        assert releaseStarted.wait(5) and not releasing.done()
        resume.set()
        recording.result(timeout=5)
        releasing.result(timeout=5)
    claim.recordText("later")
    claim.finish(StreamOutcome.COMPLETED)
    snapshot = observation.snapshot()
    assert snapshot.characterCount == 0 and snapshot.windows == () and not snapshot.active


@pytest.mark.parametrize("seconds", [float("nan"), float("inf"), 0, -1, True, 1e-20, 1e308])
def test_invalid_windows_are_rejected(seconds: float) -> None:
    """Invalid timing requests cannot introduce a broken window or unbounded gap loop."""
    with pytest.raises(ValueError):
        StreamObservationConfiguration(windowSeconds=seconds)


@pytest.mark.parametrize("windows", [0, -1, True, 1.5])
def test_invalid_retention_is_rejected(windows: int) -> None:
    """Retention is always a positive finite number of windows."""
    with pytest.raises(ValueError):
        StreamObservationConfiguration(historyWindows=windows)
