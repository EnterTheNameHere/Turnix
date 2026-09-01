import pytest

from backend.llm.errors import LlmProviderProtocolError
from backend.llm.llmTypes import LlmExecutionProfile, LlmQuery, LlmStreamEvent
from backend.llm.streamingRuntime import LlmProcessingPipeline, LlmProviderRegistry
from backend.processing.runtime import QueryItem
from backend.registration import RegistrationScope
from backend.values.committed import CommittedValueLayer


class CompletingProvider:
    def getExecutionProfile(self, *, model, providerOptions):
        return LlmExecutionProfile(contextWindowTokens=4096)

    def stream(self, request):
        yield LlmStreamEvent(eventType="delta", text="ok")
        yield LlmStreamEvent(eventType="completed", metadata={"complete": True})


class IncompleteProvider:
    def getExecutionProfile(self, *, model, providerOptions):
        return LlmExecutionProfile(contextWindowTokens=4096)

    def stream(self, request):
        yield LlmStreamEvent(eventType="delta", text="partial")


class OpaqueProvider:
    def getExecutionProfile(self, *, model, providerOptions):
        return LlmExecutionProfile(contextWindowTokens=4096)

    def stream(self, request):
        assert isinstance(request.query.payload, object)
        yield LlmStreamEvent(eventType="completed")


def _providers():
    providers = LlmProviderRegistry()
    scope = RegistrationScope()
    providers.register(scope, ownerId="good-owner", name="good", provider=CompletingProvider())
    providers.register(scope, ownerId="bad-owner", name="bad", provider=IncompleteProvider())
    providers.register(scope, ownerId="opaque-owner", name="opaque", provider=OpaqueProvider())
    scope.publish()
    return providers


def test_processing_run_commits_query_items_for_next_run():
    state = CommittedValueLayer()
    seenPrevious = []

    def invoke(capabilityId, payload):
        if capabilityId == "build-items@1":
            seenPrevious.append(payload["previousQueryItems"])
            index = len(seenPrevious)
            retained = [] if not seenPrevious[-1] else [QueryItem.fromSnapshot(seenPrevious[-1][0])]
            retained.append(
                QueryItem(
                    itemId=f"chat:{index}",
                    kind="chat",
                    content=f"chunk {index}",
                    metadata={"nested": {"values": [1, 2, 3]}},
                ),
            )
            return retained
        if capabilityId == "build-query@1":
            return {
                "formatId": "text/plain",
                "payload": "\n".join(item["content"] for item in payload["queryItems"]),
                "metadata": {"test": True},
            }
        raise AssertionError(capabilityId)

    pipeline = LlmProcessingPipeline(providers=_providers(), state=state, capabilityInvoker=invoke)
    first = pipeline.runProcessing(
        memoryKey="test",
        inputValue={"window": 1},
        buildQueryItemsCapabilityId="build-items@1",
        buildQueryCapabilityId="build-query@1",
        providerName="good",
    )
    second = pipeline.runProcessing(
        memoryKey="test",
        inputValue={"window": 2},
        buildQueryItemsCapabilityId="build-items@1",
        buildQueryCapabilityId="build-query@1",
        providerName="good",
    )

    assert first.llm.rawText == "ok"
    assert second.llm.rawText == "ok"
    assert seenPrevious[0] == []
    assert seenPrevious[1][0]["itemId"] == "chat:1"
    assert seenPrevious[1][0]["metadata"]["nested"]["values"] == [1, 2, 3]
    assert state.load("processing/test/currentqueryitems") == ["chat:1", "chat:2"]
    assert first.reusableQueryItems[0].itemId == "chat:1"
    assert [item.itemId for item in second.reusableQueryItems] == ["chat:1", "chat:2"]


def test_failed_processing_run_does_not_replace_committed_memory():
    state = CommittedValueLayer()
    mode = {"item": "accepted"}

    def invoke(capabilityId, payload):
        if capabilityId == "build-items@1":
            return [QueryItem(itemId=mode["item"], kind="test", content=mode["item"])]
        if capabilityId == "build-query@1":
            return {"formatId": "text/plain", "payload": payload["queryItems"][0]["content"]}
        raise AssertionError(capabilityId)

    pipeline = LlmProcessingPipeline(providers=_providers(), state=state, capabilityInvoker=invoke)
    pipeline.runProcessing(
        memoryKey="rollback",
        inputValue={},
        buildQueryItemsCapabilityId="build-items@1",
        buildQueryCapabilityId="build-query@1",
        providerName="good",
    )
    revisionBefore = state.revisionId("processing/rollback/currentqueryitems")
    committedBefore = state.load("processing/rollback/currentqueryitems")

    mode["item"] = "rejected"
    with pytest.raises(LlmProviderProtocolError):
        pipeline.runProcessing(
            memoryKey="rollback",
            inputValue={},
            buildQueryItemsCapabilityId="build-items@1",
            buildQueryCapabilityId="build-query@1",
            providerName="bad",
        )

    assert state.revisionId("processing/rollback/currentqueryitems") == revisionBefore
    assert state.load("processing/rollback/currentqueryitems") == committedBefore


def test_filter_may_only_select_unchanged_built_items():
    state = CommittedValueLayer()

    def invoke(capabilityId, payload):
        if capabilityId == "build-items@1":
            return [QueryItem(itemId="one", kind="test", content="original")]
        if capabilityId == "filter@1":
            return [QueryItem(itemId="one", kind="test", content="changed")]
        if capabilityId == "build-query@1":
            raise AssertionError("BUILD_QUERY must not run after invalid filtering")
        raise AssertionError(capabilityId)

    pipeline = LlmProcessingPipeline(providers=_providers(), state=state, capabilityInvoker=invoke)
    with pytest.raises(ValueError, match="modified QueryItem"):
        pipeline.runProcessing(
            memoryKey="filtering",
            inputValue={},
            buildQueryItemsCapabilityId="build-items@1",
            filterQueryItemsCapabilityId="filter@1",
            buildQueryCapabilityId="build-query@1",
            providerName="good",
        )


def test_observer_failure_is_evidence_not_processing_failure():
    providers = _providers()
    pipeline = LlmProcessingPipeline(providers=providers)

    def observer(_event):
        raise RuntimeError("display disconnected")

    result = pipeline.run(
        providerName="good",
        query=LlmQuery(formatId="text/plain", payload="hello"),
        streamObserver=observer,
    )

    assert result.rawText == "ok"
    assert len(result.observerErrors) == 2
    assert all("display disconnected" in error for error in result.observerErrors)


def test_processing_commit_does_not_require_query_payload_to_be_json_encodable():
    state = CommittedValueLayer()
    opaquePayload = object()

    def invoke(capabilityId, payload):
        if capabilityId == "build-items@1":
            return [QueryItem(itemId="opaque", kind="test", content="opaque")]
        if capabilityId == "build-query@1":
            return LlmQuery(formatId="application/x-opaque", payload=opaquePayload)
        raise AssertionError(capabilityId)

    pipeline = LlmProcessingPipeline(providers=_providers(), state=state, capabilityInvoker=invoke)
    result = pipeline.runProcessing(
        memoryKey="opaque",
        inputValue={},
        buildQueryItemsCapabilityId="build-items@1",
        buildQueryCapabilityId="build-query@1",
        providerName="opaque",
    )

    assert result.llm.query.payload is opaquePayload
    runRecord = state.load(f"processing/opaque/runs/{result.processingRunId}")
    assert runRecord["query"]["payloadType"] == "object"
    assert "payload" not in runRecord["query"]
