# file: tests/backend/context/test_codeEntryContext.py ; version: 1
import pytest

from pathlib import Path

from backend.capabilities.runtime import CapabilityRegistry
from backend.context.codeEntryContext import CodeEntryContext, CodeEntryIdentity, _LlmFacade
from backend.llm.errors import LlmProviderProtocolError
from backend.llm.llmTypes import LlmExecutionProfile, LlmQuery
from backend.llm.streamingRuntime import LlmProcessingPipeline, LlmProviderRegistry
from backend.registration import RegistrationScope
from backend.values import CommittedValueLayer, MISSING, ValueState


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



class _Io:
    def readText(self, path):
        del path
        raise AssertionError

    def readJson(self, path):
        del path
        raise AssertionError

    def readLines(self, path):
        del path
        raise AssertionError

    def writeTextAtomic(self, path, text):
        del path, text
        raise AssertionError

    def writeJsonAtomic(self, path, value):
        del path, value
        raise AssertionError


def _contextWithMemory(memory: CommittedValueLayer) -> CodeEntryContext:
    providers = LlmProviderRegistry()
    return CodeEntryContext(
        identity=CodeEntryIdentity(
            applicationId="application",
            applicationRunId="run",
            packId="pack",
            codeEntryId="entry",
            codeEntryInstanceId="entry-instance",
        ),
        packRoot=Path.cwd(),
        io=_Io(),
        capabilities=CapabilityRegistry(),
        llmProviders=providers,
        llmPipeline=LlmProcessingPipeline(providers=providers),
        memory=memory,
        registrationScope=RegistrationScope(),
        config={},
        capabilityInvoker=lambda capabilityId, payload=None: (_ for _ in ()).throw(
            AssertionError((capabilityId, payload))
        ),
    )


def test_memory_facade_commits_authoritative_value_for_later_context() -> None:
    memory = CommittedValueLayer()
    first = _contextWithMemory(memory)
    transaction = first.memory.openTransaction()
    transaction.set("chat/line/17/semantic", {"body": "hello"})
    transaction.commit()
    first.invalidate()

    second = _contextWithMemory(memory)
    try:
        assert second.memory.load("chat/line/17/semantic") == {"body": "hello"}
        assert second.memory.state("chat/line/17/semantic") is ValueState.PRESENT
        assert second.memory.revisionId("chat/line/17/semantic") == 1
    finally:
        second.invalidate()


def test_memory_facade_exposes_transactional_invalidation() -> None:
    memory = CommittedValueLayer()
    seed = memory.openTransaction()
    seed.set("chat/line/17/semantic", {"body": "old"})
    seed.commit()

    context = _contextWithMemory(memory)
    transaction = context.memory.openTransaction()
    transaction.invalidate("chat/line/17/semantic")

    assert transaction.state("chat/line/17/semantic") is ValueState.INVALIDATED
    assert context.memory.state("chat/line/17/semantic") is ValueState.PRESENT

    transaction.commit()

    assert context.memory.state("chat/line/17/semantic") is ValueState.INVALIDATED
    assert context.memory.load("chat/line/17/semantic") is MISSING
    assert context.memory.revisionId("chat/line/17/semantic") == 2
    context.invalidate()


def test_memory_authority_cannot_escape_invalidated_context() -> None:
    memory = CommittedValueLayer()
    context = _contextWithMemory(memory)
    facade = context.memory
    transaction = facade.openTransaction()
    transaction.set("chat/line/17/semantic", {"body": "staged"})

    context.invalidate()

    with pytest.raises(RuntimeError, match="Context is no longer valid"):
        facade.load("chat/line/17/semantic")
    with pytest.raises(RuntimeError, match="Context is no longer valid"):
        transaction.load("chat/line/17/semantic")
    with pytest.raises(RuntimeError, match="Context is no longer valid"):
        transaction.commit()

    # The transaction never crossed the authoritative boundary.
    assert memory.load("chat/line/17/semantic") is MISSING
