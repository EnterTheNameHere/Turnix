import pytest

from backend.llm.errors import LlmProviderProtocolError
from backend.llm.llmTypes import LlmExecutionProfile, LlmQuery, LlmStreamEvent
from backend.llm.streamingRuntime import LlmProviderRegistry, StreamingLlmPipeline
from backend.registration import RegistrationScope


class Provider:
    def getExecutionProfile(self, *, model, providerOptions):
        assert model == "model-a"
        assert providerOptions["temperature"] == 0.5
        return LlmExecutionProfile(contextWindowTokens=8192, metadata={"providerProfile": "test"})

    def stream(self, request):
        assert request.query.payload == "hello"
        yield LlmStreamEvent(eventType="delta", text="a")
        yield LlmStreamEvent(eventType="delta", text="b")
        yield LlmStreamEvent(eventType="completed", metadata={"done": True})


class IncompleteProvider:
    def getExecutionProfile(self, *, model, providerOptions):
        return LlmExecutionProfile(contextWindowTokens=4096)

    def stream(self, request):
        yield LlmStreamEvent(eventType="delta", text="partial")


class LateEventProvider:
    def getExecutionProfile(self, *, model, providerOptions):
        return LlmExecutionProfile(contextWindowTokens=4096)

    def stream(self, request):
        yield LlmStreamEvent(eventType="completed")
        yield LlmStreamEvent(eventType="delta", text="too late")


def _registry(name, provider):
    providers = LlmProviderRegistry()
    scope = RegistrationScope()
    providers.register(scope, ownerId=f"{name}-owner", name=name, provider=provider)
    scope.publish()
    return providers


def test_stream_is_consumed_incrementally_and_preserves_execution_evidence():
    observed = []
    result = StreamingLlmPipeline(providers=_registry("test", Provider())).run(
        providerName="test",
        query=LlmQuery(formatId="text/plain", payload="hello"),
        model="model-a",
        providerOptions={"temperature": 0.5},
        streamObserver=observed.append,
    )

    assert result.rawText == "ab"
    assert result.providerOwnerId == "test-owner"
    assert result.executionProfile.contextWindowTokens == 8192
    assert result.executionProfile.metadata["providerProfile"] == "test"
    assert result.providerMetadata["done"] is True
    assert [event.eventType for event in observed] == ["delta", "delta", "completed"]


def test_stream_eof_without_completed_event_is_protocol_failure():
    pipeline = StreamingLlmPipeline(providers=_registry("incomplete", IncompleteProvider()))

    with pytest.raises(LlmProviderProtocolError, match="without a completed event"):
        pipeline.run(
            providerName="incomplete",
            query=LlmQuery(formatId="text/plain", payload="hello"),
        )


def test_provider_must_not_emit_after_completed_event():
    pipeline = StreamingLlmPipeline(providers=_registry("late", LateEventProvider()))

    with pytest.raises(LlmProviderProtocolError, match="after completion"):
        pipeline.run(
            providerName="late",
            query=LlmQuery(formatId="text/plain", payload="hello"),
        )
