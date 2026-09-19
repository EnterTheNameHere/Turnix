# file: tests/backend/llm/test_cancellation.py ; version: 2
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from backend.llm.llmTypes import LlmCallRequest, LlmExecutionProfile, LlmQuery, LlmStreamEvent
from backend.llm.streamingRuntime import LlmProcessingPipeline, LlmProviderRegistry
from backend.orchestration import CancellationSignal, ExecutionCancelled
from backend.processing.runtime import QueryItem
from backend.registration import RegistrationScope
from backend.values import CommittedValueLayer, MISSING

if TYPE_CHECKING:
    from collections.abc import Iterator


class _CancellingProvider:
    """Provider that requests cancellation after producing one partial delta."""

    def getExecutionProfile(
        self,
        *,
        model: str | None,
        providerOptions: object,
    ) -> LlmExecutionProfile:
        """Returns one deterministic execution profile."""
        del model, providerOptions
        return LlmExecutionProfile(contextWindowTokens=4096)

    def stream(self, request: LlmCallRequest) -> Iterator[LlmStreamEvent]:
        """Requests cancellation between partial and later provider events."""
        yield LlmStreamEvent(eventType="delta", text="partial")
        assert request.cancellationSignal is not None
        request.cancellationSignal.request()
        yield LlmStreamEvent(eventType="delta", text="must-not-be-accepted")
        yield LlmStreamEvent(eventType="completed")


def _providers() -> LlmProviderRegistry:
    """Returns one registry containing the deterministic cancelling provider."""
    providers = LlmProviderRegistry()
    scope = RegistrationScope()
    providers.register(
        scope,
        ownerId="cancel-owner",
        name="cancel",
        provider=_CancellingProvider(),
    )
    scope.publish()
    return providers


def test_direct_llm_execution_reports_intentional_cancellation() -> None:
    """Provider cancellation does not degrade into protocol or provider failure."""
    signal = CancellationSignal()
    pipeline = LlmProcessingPipeline(providers=_providers())

    with pytest.raises(ExecutionCancelled):
        pipeline.run(
            providerName="cancel",
            query=LlmQuery(formatId="text/plain", payload="question"),
            cancellationSignal=signal,
        )

    assert signal.requested is True


def test_cancelled_processing_run_discards_speculative_processing_state() -> None:
    """Cancelled LLM work aborts its ProcessingRun transaction and reports cancellation."""
    state = CommittedValueLayer()
    signal = CancellationSignal()
    traceReasons: list[str] = []

    def invoke(capabilityId: str, payload: object, memoryView: object) -> object:
        """Builds deterministic processing input around the cancellation provider."""
        del memoryView
        if not isinstance(payload, dict):
            raise TypeError("Test pipeline payload must be an object.")
        if capabilityId == "build-items@1":
            return [QueryItem(itemId="one", kind="test", content="question")]
        if capabilityId == "build-query@1":
            queryItems = payload["queryItems"]
            if not isinstance(queryItems, list):
                raise TypeError("Test queryItems must be a list.")
            first = queryItems[0]
            if not isinstance(first, dict):
                raise TypeError("Test QueryItem snapshot must be an object.")
            return {"formatId": "text/plain", "payload": first["content"]}
        raise AssertionError(capabilityId)

    def trace(reason: str, _attributes: dict[str, object]) -> None:
        """Captures processing lifecycle trace reasons."""
        traceReasons.append(reason)

    pipeline = LlmProcessingPipeline(
        providers=_providers(),
        state=state,
        capabilityInvoker=invoke,
        trace=trace,
    )

    with pytest.raises(ExecutionCancelled):
        pipeline.runProcessing(
            memoryKey="cancelled",
            inputValue={},
            buildQueryItemsCapabilityId="build-items@1",
            buildQueryCapabilityId="build-query@1",
            providerName="cancel",
            cancellationSignal=signal,
        )

    assert "processing-run-cancelled" in traceReasons
    assert "processing-run-failed" not in traceReasons
    assert state.load("processing/cancelled/currentqueryitems") is MISSING
    assert state.revisionId("processing/cancelled/currentqueryitems") == 0
