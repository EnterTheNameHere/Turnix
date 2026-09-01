from backend.llm.llmTypes import LlmExecutionProfile, LlmQuery, LlmStreamEvent
from backend.llm.streamingRuntime import LlmProviderRegistry, StreamingLlmPipeline
from backend.registration import RegistrationScope


class Provider:
    def getExecutionProfile(self, *, model, providerOptions):
        del model, providerOptions
        return LlmExecutionProfile()

    def stream(self, request):
        assert request.query.payload == "hello"
        yield LlmStreamEvent(eventType="delta", text="a")
        yield LlmStreamEvent(eventType="delta", text="b")
        yield LlmStreamEvent(eventType="completed", metadata={"done": True})


def test_stream_is_consumed_incrementally_and_requires_completion():
    providers = LlmProviderRegistry()
    scope = RegistrationScope()
    providers.register(scope, ownerId="provider-entry", name="test", provider=Provider())
    scope.publish()
    observed = []
    result = StreamingLlmPipeline(providers=providers).run(
        providerName="test",
        query=LlmQuery(formatId="text/plain", payload="hello"),
        streamObserver=observed.append,
    )
    assert result.rawText == "ab"
    assert [event.eventType for event in observed] == ["delta", "delta", "completed"]
