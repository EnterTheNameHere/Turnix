# file: tests/backend/llm/test_providerTiming.py ; version: 2
"""Tests for shared LLM provider-execution timing evidence."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

from backend.llm.llmTypes import LlmExecutionProfile, LlmQuery, LlmStreamEvent
from backend.llm.streamingRuntime import LlmProcessingPipeline, LlmProviderRegistry
from backend.processing.runtime import QueryItem
from backend.registration import RegistrationScope
from backend.values.committed import CommittedValueLayer


class _Provider:
    """Deterministic provider used to exercise the generic stream boundary."""

    def getExecutionProfile(self, *, model, providerOptions) -> LlmExecutionProfile:
        """Returns a minimal execution profile independent of model selection."""
        return LlmExecutionProfile(contextWindowTokens=4096)

    def stream(self, request) -> Iterator[LlmStreamEvent]:
        """Yields one response delta followed by a terminal completion event."""
        yield LlmStreamEvent(eventType="delta", text="answer")
        yield LlmStreamEvent(eventType="completed", metadata={"done": True})


def _providers() -> LlmProviderRegistry:
    """Creates a published registry containing the deterministic test provider."""
    providers = LlmProviderRegistry()
    scope = RegistrationScope()
    providers.register(scope, ownerId="timing-owner", name="timed", provider=_Provider())
    scope.publish()
    return providers


def _patchClock(monkeypatch, wallTimes: tuple[int, int], monotonicTimes: tuple[int, int]) -> None:
    """Replaces only the LLM runtime clock reference with deterministic clocks.

    Replacing methods on Python's shared ``time`` module would also alter
    ``uuid.uuid7()`` internals used when a ProcessingRun creates its runtime ID.
    The test therefore replaces the module reference owned by streamingRuntime
    rather than mutating the process-wide module object.
    """
    wallIterator = iter(wallTimes)
    monotonicIterator = iter(monotonicTimes)
    clock = SimpleNamespace(
        time_ns=lambda: next(wallIterator),
        monotonic_ns=lambda: next(monotonicIterator),
    )
    monkeypatch.setattr("backend.llm.streamingRuntime.time", clock)


def test_direct_run_reports_wall_endpoints_and_monotonic_duration(monkeypatch) -> None:
    """Direct provider execution exposes timing using the process-evidence field names."""
    _patchClock(monkeypatch, (1_000, 1_900), (50_000, 50_700))

    result = LlmProcessingPipeline(providers=_providers()).run(
        providerName="timed",
        query=LlmQuery(formatId="text/plain", payload="question"),
    )

    assert result.startedTimeNs == 1_000
    assert result.endedTimeNs == 1_900
    assert result.durationNs == 700
    assert result.timingSnapshot() == {
        "startedTimeNs": 1_000,
        "endedTimeNs": 1_900,
        "durationNs": 700,
    }


def test_processing_run_persists_provider_timing(monkeypatch) -> None:
    """Authoritative ProcessingRun evidence retains the exact provider timing snapshot."""
    _patchClock(monkeypatch, (2_000, 2_800), (80_000, 80_600))
    state = CommittedValueLayer()

    def invoke(capabilityId: str, payload: object, memoryView: object) -> object:
        """Builds one deterministic QueryItem and exact plain-text model query."""
        if capabilityId == "build-items@1":
            return [QueryItem(itemId="question", kind="test", content="question")]
        if capabilityId == "build-query@1":
            return {"formatId": "text/plain", "payload": "question"}
        raise AssertionError(capabilityId)

    result = LlmProcessingPipeline(
        providers=_providers(),
        state=state,
        capabilityInvoker=invoke,
    ).runProcessing(
        memoryKey="timing",
        inputValue={},
        buildQueryItemsCapabilityId="build-items@1",
        buildQueryCapabilityId="build-query@1",
        providerName="timed",
    )

    runRecord = state.load(f"processing/timing/runs/{result.processingRunId}")
    assert runRecord["providerTiming"] == {
        "startedTimeNs": 2_000,
        "endedTimeNs": 2_800,
        "durationNs": 600,
    }
    assert runRecord["providerTiming"] == result.llm.timingSnapshot()


def test_completion_receives_same_provider_timing_as_persistent_evidence(monkeypatch) -> None:
    """Completion consumers see the same shared timing authority persisted for the run."""
    _patchClock(monkeypatch, (3_000, 3_500), (90_000, 90_400))
    state = CommittedValueLayer()
    seenTiming: dict[str, int] | None = None

    def invoke(capabilityId: str, payload: object, memoryView: object) -> object:
        """Builds the query and captures timing delivered to completion."""
        nonlocal seenTiming
        if capabilityId == "build-items@1":
            return [QueryItem(itemId="question", kind="test", content="question")]
        if capabilityId == "build-query@1":
            return {"formatId": "text/plain", "payload": "question"}
        if capabilityId == "complete@1":
            assert isinstance(payload, dict)
            llm = payload["llm"]
            assert isinstance(llm, dict)
            timing = llm["timing"]
            assert isinstance(timing, dict)
            seenTiming = timing
            return None
        raise AssertionError(capabilityId)

    result = LlmProcessingPipeline(
        providers=_providers(),
        state=state,
        capabilityInvoker=invoke,
    ).runProcessing(
        memoryKey="completiontiming",
        inputValue={},
        buildQueryItemsCapabilityId="build-items@1",
        buildQueryCapabilityId="build-query@1",
        completionCapabilityId="complete@1",
        providerName="timed",
    )

    runRecord = state.load(f"processing/completiontiming/runs/{result.processingRunId}")
    assert seenTiming == runRecord["providerTiming"]
