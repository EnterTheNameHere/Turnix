import pytest

from backend.context.codeEntryContext import _LlmFacade
from backend.llm.errors import LlmProviderProtocolError
from backend.llm.llmTypes import LlmExecutionProfile, LlmQuery
from backend.llm.streamingRuntime import LlmProcessingPipeline, LlmProviderRegistry
from backend.registration import RegistrationScope


class _Estimator:
    def __init__(self, result):
        self.result = result
        self.queries = []

    def estimateInputTokens(self, query):
        self.queries.append(query)
        return self.result


class _Provider:
    def __init__(self, estimator):
        self.estimator = estimator
        self.calls = []

    def getExecutionProfile(self, *, model, providerOptions):
        self.calls.append((model, providerOptions))
        return LlmExecutionProfile(contextWindowTokens=4096, tokenEstimator=self.estimator)

    def stream(self, request):
        del request
        return iter(())


def _facade(provider):
    registry = LlmProviderRegistry()
    scope = RegistrationScope()
    registry.register(scope, ownerId="owner", name="provider", provider=provider)
    scope.publish()
    return _LlmFacade(
        ownerId="caller",
        registry=registry,
        scope=RegistrationScope(),
        pipeline=LlmProcessingPipeline(providers=registry),
        requireValid=lambda: None,
        allowRegistration=False,
    )


def test_estimateInputTokens_forwards_selected_execution_and_query():
    estimator = _Estimator(12)
    provider = _Provider(estimator)
    facade = _facade(provider)
    query = LlmQuery(formatId="text/plain", payload="hello")

    result = facade.estimateInputTokens(
        providerName="provider",
        query=query,
        model="model-a",
        providerOptions={"temperature": 0.25},
    )

    assert result == 12
    assert provider.calls[0][0] == "model-a"
    assert provider.calls[0][1]["temperature"] == 0.25
    assert estimator.queries == [query]


def test_estimateInputTokens_rejects_missing_or_invalid_estimator():
    facade = _facade(_Provider(None))
    with pytest.raises(RuntimeError, match="does not expose"):
        facade.estimateInputTokens(
            providerName="provider",
            query=LlmQuery(formatId="text/plain", payload="hello"),
        )

    facade = _facade(_Provider(_Estimator(True)))
    with pytest.raises(LlmProviderProtocolError, match="invalid value"):
        facade.estimateInputTokens(
            providerName="provider",
            query=LlmQuery(formatId="text/plain", payload="hello"),
        )
