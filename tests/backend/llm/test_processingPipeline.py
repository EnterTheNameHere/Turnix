import pytest

from backend.llm.errors import LlmProviderProtocolError
from backend.llm.llmTypes import LlmExecutionProfile, LlmQuery, LlmStreamEvent
from backend.llm.streamingRuntime import LlmProcessingPipeline, LlmProviderRegistry
from backend.processing.runtime import QueryItem
from backend.registration import RegistrationScope
from backend.values.committed import CommittedValueLayer
from backend.values.sentinels import MISSING


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


def test_filter_selects_current_query_without_erasing_reusable_memory():
    state = CommittedValueLayer()

    def invoke(capabilityId, payload):
        if capabilityId == "build-items@1":
            return [
                QueryItem(itemId="keep", kind="test", content="included"),
                QueryItem(itemId="later", kind="test", content="reusable only"),
            ]
        if capabilityId == "filter@1":
            return [payload["queryItems"][0]]
        if capabilityId == "build-query@1":
            assert [item["itemId"] for item in payload["queryItems"]] == ["keep"]
            return {"formatId": "text/plain", "payload": "included"}
        raise AssertionError(capabilityId)

    result = LlmProcessingPipeline(
        providers=_providers(),
        state=state,
        capabilityInvoker=invoke,
    ).runProcessing(
        memoryKey="selection",
        inputValue={},
        buildQueryItemsCapabilityId="build-items@1",
        filterQueryItemsCapabilityId="filter@1",
        buildQueryCapabilityId="build-query@1",
        providerName="good",
    )

    assert [item.itemId for item in result.queryItems] == ["keep"]
    assert [item.itemId for item in result.reusableQueryItems] == ["keep", "later"]
    assert state.load("processing/selection/currentqueryitems") == ["keep", "later"]
    runRecord = state.load(f"processing/selection/runs/{result.processingRunId}")
    assert runRecord["acceptedQueryItemIds"] == ["keep"]
    assert runRecord["reusableQueryItemIds"] == ["keep", "later"]


def test_execution_profile_is_resolved_before_query_item_selection_and_building():
    state = CommittedValueLayer()
    seenStages = []

    class ProfileProvider:
        def getExecutionProfile(self, *, model, providerOptions):
            seenStages.append("profile")
            assert model == "model-a"
            assert providerOptions["temperature"] == 0.25
            return LlmExecutionProfile(contextWindowTokens=8192, metadata={"profile": "resolved"})

        def stream(self, request):
            seenStages.append("stream")
            yield LlmStreamEvent(eventType="completed")

    providers = LlmProviderRegistry()
    scope = RegistrationScope()
    providers.register(scope, ownerId="profile-owner", name="profile", provider=ProfileProvider())
    scope.publish()

    def invoke(capabilityId, payload):
        execution = payload["execution"]
        assert execution["contextWindowTokens"] == 8192
        assert execution["metadata"] == {"profile": "resolved"}
        if capabilityId == "build-items@1":
            seenStages.append("items")
            return [QueryItem(itemId="one", kind="test", content="one")]
        if capabilityId == "filter@1":
            seenStages.append("filter")
            return payload["queryItems"]
        if capabilityId == "build-query@1":
            seenStages.append("query")
            return {"formatId": "text/plain", "payload": "one"}
        raise AssertionError(capabilityId)

    LlmProcessingPipeline(providers=providers, state=state, capabilityInvoker=invoke).runProcessing(
        memoryKey="profileorder",
        inputValue={},
        buildQueryItemsCapabilityId="build-items@1",
        filterQueryItemsCapabilityId="filter@1",
        buildQueryCapabilityId="build-query@1",
        providerName="profile",
        model="model-a",
        providerOptions={"temperature": 0.25},
    )

    assert seenStages == ["profile", "items", "filter", "query", "stream"]


def test_reused_query_item_identity_rejects_content_drift():
    state = CommittedValueLayer()
    content = {"value": "first"}

    def invoke(capabilityId, payload):
        if capabilityId == "build-items@1":
            return [QueryItem(itemId="stable-id", kind="test", content=content["value"])]
        if capabilityId == "build-query@1":
            return {"formatId": "text/plain", "payload": payload["queryItems"][0]["content"]}
        raise AssertionError(capabilityId)

    pipeline = LlmProcessingPipeline(providers=_providers(), state=state, capabilityInvoker=invoke)
    pipeline.runProcessing(
        memoryKey="identity",
        inputValue={},
        buildQueryItemsCapabilityId="build-items@1",
        buildQueryCapabilityId="build-query@1",
        providerName="good",
    )

    content["value"] = "changed"
    with pytest.raises(RuntimeError, match="different content"):
        pipeline.runProcessing(
            memoryKey="identity",
            inputValue={},
            buildQueryItemsCapabilityId="build-items@1",
            buildQueryCapabilityId="build-query@1",
            providerName="good",
        )

    assert state.load("processing/identity/currentqueryitems") == ["stable-id"]


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


def test_finalize_input_is_forwarded_without_becoming_processing_memory():
    state = CommittedValueLayer()
    finalizeInput = {"diagnostic": {"text": "prepared side material"}}

    def invoke(capabilityId, payload):
        if capabilityId == "build-items@1":
            return [QueryItem(itemId="final-input", kind="test", content="final")]
        if capabilityId == "build-query@1":
            return {"formatId": "text/plain", "payload": "final"}
        if capabilityId == "finalize@1":
            assert payload["finalizeInput"] is finalizeInput
            return {"saved": True}
        raise AssertionError(capabilityId)

    result = LlmProcessingPipeline(
        providers=_providers(),
        state=state,
        capabilityInvoker=invoke,
    ).runProcessing(
        memoryKey="finalinput",
        inputValue={},
        buildQueryItemsCapabilityId="build-items@1",
        buildQueryCapabilityId="build-query@1",
        finalizeCapabilityId="finalize@1",
        finalizeInput=finalizeInput,
        providerName="good",
    )

    assert result.finalizeResult == {"saved": True}
    runRecord = state.load(f"processing/finalinput/runs/{result.processingRunId}")
    assert "finalizeInput" not in runRecord


def test_finalize_failure_aborts_processing_state():
    state = CommittedValueLayer()

    def invoke(capabilityId, payload):
        if capabilityId == "build-items@1":
            return [QueryItem(itemId="final", kind="test", content="final")]
        if capabilityId == "build-query@1":
            return {"formatId": "text/plain", "payload": "final"}
        if capabilityId == "finalize@1":
            assert payload["llm"]["response"]["rawText"] == "ok"
            raise RuntimeError("persistence failed")
        raise AssertionError(capabilityId)

    pipeline = LlmProcessingPipeline(providers=_providers(), state=state, capabilityInvoker=invoke)
    with pytest.raises(RuntimeError, match="persistence failed"):
        pipeline.runProcessing(
            memoryKey="finalize",
            inputValue={},
            buildQueryItemsCapabilityId="build-items@1",
            buildQueryCapabilityId="build-query@1",
            finalizeCapabilityId="finalize@1",
            providerName="good",
        )

    assert state.load("processing/finalize/currentqueryitems") is MISSING
    assert state.revisionId("processing/finalize/currentqueryitems") == 0
