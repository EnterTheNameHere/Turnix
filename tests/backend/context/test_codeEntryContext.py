# file: tests/backend/context/test_codeEntryContext.py ; version: 6
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
        memory=CommittedValueLayer(),
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
            packVersion="1.0.0",
            codeEntryId="entry",
            codeEntryInstanceId="entry-instance",
            sourceSha256="source-sha",
            implementationId="implementation-sha",
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



def test_memory_write_stamps_current_code_entry_producer_metadata() -> None:
    memory = CommittedValueLayer()
    context = _contextWithMemory(memory)

    transaction = context.memory.openTransaction()
    transaction.set(
        "derived/value",
        {"answer": 42},
        validity={"inputRevision": 7},
        provenance={"source": "fixture"},
    )
    transaction.commit()

    assert memory.metadata("derived/value") == {
        "formatId": "actant.derived-value-metadata@1",
        "producer": {
            "packId": "pack",
            "packVersion": "1.0.0",
            "codeEntryId": "entry",
            "sourceSha256": "source-sha",
            "implementationFormat": "python-source@1",
            "implementationId": "implementation-sha",
        },
        "validity": {"inputRevision": 7},
        "provenance": {"source": "fixture"},
    }
    assert context.memory.isReusable(
        "derived/value",
        validity={"inputRevision": 7},
    ) is True
    assert context.memory.isReusable(
        "derived/value",
        validity={"inputRevision": 8},
    ) is False
    context.invalidate()


def test_memory_reuse_requires_same_producer_implementation() -> None:
    memory = CommittedValueLayer()
    first = _contextWithMemory(memory)
    transaction = first.memory.openTransaction()
    transaction.set("derived/value", "first", validity={"source": "same"})
    transaction.commit()
    first.invalidate()

    providers = LlmProviderRegistry()
    second = CodeEntryContext(
        identity=CodeEntryIdentity(
            applicationId="application",
            applicationRunId="run-2",
            packId="pack",
            packVersion="1.0.0",
            codeEntryId="entry",
            codeEntryInstanceId="entry-instance-2",
            sourceSha256="different-source-sha",
            implementationFormat="python-source@1",
            implementationId="different-implementation-sha",
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
    try:
        assert second.memory.isReusable(
            "derived/value",
            validity={"source": "same"},
        ) is False
    finally:
        second.invalidate()


def test_memory_describe_reports_staged_and_committed_views() -> None:
    memory = CommittedValueLayer()
    context = _contextWithMemory(memory)
    transaction = context.memory.openTransaction()
    transaction.set("derived/value", {"large": "payload"}, validity={"v": 1})

    staged = transaction.describe("derived/value")
    assert staged["revisionId"] == 0
    assert staged["state"] == "present"
    assert staged["staged"] is True
    assert staged["metadata"]["validity"] == {"v": 1}

    transaction.commit()

    committed = context.memory.describe("derived/value")
    assert committed["revisionId"] == 1
    assert committed["state"] == "present"
    assert committed["metadata"]["validity"] == {"v": 1}
    context.invalidate()
