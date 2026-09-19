# file: tests/backend/llm/test_cancellation.py ; version: 1
import pytest

from backend.llm.llmTypes import LlmExecutionProfile, LlmQuery, LlmStreamEvent
from backend.llm.streamingRuntime import LlmProcessingPipeline, LlmProviderRegistry
from backend.orchestration import CancellationSignal, ExecutionCancelled
from backend.processing.runtime import QueryItem
from backend.registration import RegistrationScope
from backend.values import CommittedValueLayer, MISSING


class _CancellingProvider:
    """Provider that requests cancellation after producing one partial delta."""

    def getExecutionProfile(self, *, model, providerOptions) -> LlmExecutionProfile:
        """Returns one deterministic execution profile."""
        return LlmExecutionProfile(contextWindowTokens=4096)

    def stream(self, request):
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
    """Cancelled LLM work aborts its ProcessingRun transaction."""
    state = CommittedValueLayer()
    signal = CancellationSignal()

    def invoke(capabilityId, payload, memoryView):
        """Builds deterministic processing input around the cancellation provider."""
        if capabilityId == "build-items@1":
            return [QueryItem(itemId="one", kind="test", content="question")]
        if capabilityId == "build-query@1":
            return {"formatId": "text/plain", "payload": payload["queryItems"][0]["content"]}
        raise AssertionError(capabilityId)

    pipeline = LlmProcessingPipeline(
        providers=_providers(),
        state=state,
        capabilityInvoker=invoke,
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

    assert state.load("processing/cancelled/currentqueryitems") is MISSING
    assert state.revisionId("processing/cancelled/currentqueryitems") == 0
