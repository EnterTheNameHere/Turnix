import pytest

from backend.llm.errors import LlmProviderProtocolError
from backend.llm.llmTypes import LlmExecutionProfile, LlmStreamEvent
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


def _providers():
    providers = LlmProviderRegistry()
    scope = RegistrationScope()
    providers.register(scope, ownerId="good-owner", name="good", provider=CompletingProvider())
    providers.register(scope, ownerId="bad-owner", name="bad", provider=IncompleteProvider())
    scope.publish()
    return providers


def test_processing_run_commits_query_items_for_next_run():
    state = CommittedValueLayer()
    seenPrevious = []

    def invoke(capabilityId, payload):
        if capabilityId == "build-items@1":
            seenPrevious.append(payload["previousQueryItems"])
            index = len(seenPrevious)
            return [
                QueryItem(
                    itemId=f"chat:{index}",
                    kind="chat",
                    content=f"chunk {index}",
                    metadata={"nested": {"values": [1, 2, 3]}},
                ),
            ]
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
    assert state.load("processing/test/queryitems")[0]["itemId"] == "chat:2"


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
    revisionBefore = state.revisionId("processing/rollback/queryitems")
    committedBefore = state.load("processing/rollback/queryitems")

    mode["item"] = "rejected"
    with pytest.raises(LlmProviderProtocolError):
        pipeline.runProcessing(
            memoryKey="rollback",
            inputValue={},
            buildQueryItemsCapabilityId="build-items@1",
            buildQueryCapabilityId="build-query@1",
            providerName="bad",
        )

    assert state.revisionId("processing/rollback/queryitems") == revisionBefore
    assert state.load("processing/rollback/queryitems") == committedBefore
