from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer
from backend.llm.errors import LlmProviderProtocolError
from backend.llm.llmTypes import LlmCallRequest, LlmExecutionProfile, LlmQuery, LlmStreamEvent, LlmStreamProvider
from backend.processing.runtime import ProcessingRun, ProcessingStage, QueryItem, plainImmutableValue
from backend.registration import Registration, RegistrationRegistry, RegistrationScope
from backend.values.sentinels import MISSING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from backend.values.committed import CommittedValueLayer, CommittedValueTransaction

__all__ = [
    "LlmProcessingPipeline",
    "LlmProcessingResult",
    "LlmProviderRegistry",
    "StreamingLlmPipeline",
    "StreamingLlmResult",
]


class LlmProviderRegistry:
    """Published registry of provider-neutral streaming LLM providers."""

    def __init__(self) -> None:
        self._registry: RegistrationRegistry[LlmStreamProvider] = RegistrationRegistry()

    def register(self, scope: RegistrationScope, *, ownerId: str, name: str, provider: LlmStreamProvider) -> None:
        if not callable(getattr(provider, "stream", None)) or not callable(getattr(provider, "getExecutionProfile", None)):
            raise TypeError("provider must satisfy the LlmStreamProvider contract.")
        scope.register(self._registry, ownerId=ownerId, name=name, value=provider)

    def requireRegistration(self, name: str) -> Registration[LlmStreamProvider]:
        return self._registry.require(name)

    def require(self, name: str) -> LlmStreamProvider:
        return self.requireRegistration(name).value

    def unregisterOwnedBy(self, ownerId: str) -> None:
        self._registry.unregisterOwnedBy(ownerId)


@dataclass(frozen=True, slots=True)
class StreamingLlmResult:
    query: LlmQuery
    model: str | None
    providerName: str
    providerOwnerId: str
    providerOptions: Mapping[str, ImmutableValue]
    executionProfile: LlmExecutionProfile
    rawText: str
    providerMetadata: Mapping[str, ImmutableValue] = field(default_factory=dict)
    observerErrors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LlmProcessingResult:
    """Completed ProcessingRun plus streamed provider and finalization evidence."""

    processingRunId: str
    queryItems: tuple[QueryItem, ...]
    reusableQueryItems: tuple[QueryItem, ...]
    llm: StreamingLlmResult
    finalizeResult: object | None = None


class LlmProcessingPipeline:
    """Reusable staged LLM pipeline with optional ApplicationRun committed memory.

    Reusable QueryItems are committed individually and referenced by identity so
    overlapping ProcessingRuns do not repeatedly commit the same large source
    material. Filtering affects only the accepted query for the current run; it
    does not erase reusable items prepared by BUILD_QUERY_ITEMS.

    Optional application finalization executes after all authoritative state has
    been staged and validated, but before the outer transaction commits. A
    finalization failure therefore aborts the ProcessingRun rather than leaving
    an authoritative success whose required application-side result was not
    produced.
    """

    def __init__(
        self,
        *,
        providers: LlmProviderRegistry,
        state: CommittedValueLayer | None = None,
        capabilityInvoker: Callable[[str, object | None], object] | None = None,
        trace: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        self._providers = providers
        self._state = state
        self._capabilityInvoker = capabilityInvoker
        self._trace = trace

    def run(
        self,
        *,
        providerName: str,
        query: LlmQuery,
        model: str | None = None,
        providerOptions: Mapping[str, ImmutableValue] | None = None,
        streamObserver: Callable[[LlmStreamEvent], None] | None = None,
    ) -> StreamingLlmResult:
        registration, provider, options, profile = self._resolveExecution(
            providerName=providerName,
            model=model,
            providerOptions=providerOptions,
        )
        return self._streamResolved(
            registration=registration,
            provider=provider,
            profile=profile,
            options=options,
            query=query,
            model=model,
            streamObserver=streamObserver,
        )

    def runProcessing(
        self,
        *,
        memoryKey: str,
        inputValue: object,
        buildQueryItemsCapabilityId: str,
        buildQueryCapabilityId: str,
        providerName: str,
        model: str | None = None,
        providerOptions: Mapping[str, ImmutableValue] | None = None,
        filterQueryItemsCapabilityId: str | None = None,
        finalizeCapabilityId: str | None = None,
        finalizeInput: object | None = None,
        streamObserver: Callable[[LlmStreamEvent], None] | None = None,
    ) -> LlmProcessingResult:
        if self._state is None or self._capabilityInvoker is None:
            raise RuntimeError("runProcessing() requires committed state and a capability invoker.")
        if type(memoryKey) is not str or not memoryKey or not memoryKey.replace("-", "").replace("_", "").isalnum() or not memoryKey.islower():
            raise ValueError("memoryKey must be a lowercase Value-address-safe identifier.")

        transaction = self._state.openTransaction()
        run = ProcessingRun(pipelineId=f"llm:{memoryKey}", transaction=transaction)
        currentItemsAddress = f"processing/{memoryKey}/currentqueryitems"
        committed = False
        try:
            run.enterStage(ProcessingStage.RESOLVE_EXECUTION_PROFILE)
            registration, provider, options, profile = self._resolveExecution(
                providerName=providerName,
                model=model,
                providerOptions=providerOptions,
            )
            executionSnapshot = {
                "providerName": providerName,
                "providerOwnerId": registration.ownerId,
                "model": model,
                "providerOptions": plainImmutableValue(options),
                "contextWindowTokens": profile.contextWindowTokens,
                "metadata": plainImmutableValue(profile.metadata),
            }

            previousSnapshots = self._loadCurrentQueryItems(
                transaction=transaction,
                memoryKey=memoryKey,
                currentItemsAddress=currentItemsAddress,
            )

            run.enterStage(ProcessingStage.BUILD_QUERY_ITEMS)
            built = self._capabilityInvoker(
                buildQueryItemsCapabilityId,
                {
                    "input": inputValue,
                    "previousQueryItems": previousSnapshots,
                    "execution": executionSnapshot,
                },
            )
            reusableItems = self._requireQueryItems(built, stage="BUILD_QUERY_ITEMS")
            self._stageReusableQueryItems(transaction, memoryKey=memoryKey, items=reusableItems)

            acceptedItems = reusableItems
            if filterQueryItemsCapabilityId is not None:
                run.enterStage(ProcessingStage.FILTER_QUERY_ITEMS)
                filtered = self._capabilityInvoker(
                    filterQueryItemsCapabilityId,
                    {
                        "input": inputValue,
                        "queryItems": [item.snapshot() for item in reusableItems],
                        "execution": executionSnapshot,
                    },
                )
                acceptedItems = self._requireQueryItems(filtered, stage="FILTER_QUERY_ITEMS")
                self._requireFilteredSubset(reusableItems, acceptedItems)
            run.queryItems = acceptedItems

            run.enterStage(ProcessingStage.BUILD_QUERY)
            builtQuery = self._capabilityInvoker(
                buildQueryCapabilityId,
                {
                    "input": inputValue,
                    "queryItems": [item.snapshot() for item in acceptedItems],
                    "execution": executionSnapshot,
                },
            )
            query = self._requireQuery(builtQuery)

            run.enterStage(ProcessingStage.PREPARE_PROVIDER_CALL)
            llmResult = self._streamResolved(
                registration=registration,
                provider=provider,
                profile=profile,
                options=options,
                query=query,
                model=model,
                streamObserver=streamObserver,
                processingRun=run,
            )

            run.enterStage(ProcessingStage.UPDATE_QUERY_ITEMS)
            transaction.set(currentItemsAddress, [item.itemId for item in reusableItems])
            transaction.set(
                f"processing/{memoryKey}/runs/{run.processingRunId}",
                {
                    "processingRunId": run.processingRunId,
                    "reusableQueryItemIds": [item.itemId for item in reusableItems],
                    "acceptedQueryItemIds": [item.itemId for item in acceptedItems],
                    "query": self._queryEvidence(llmResult.query),
                    "response": {"rawText": llmResult.rawText},
                    "execution": executionSnapshot,
                    "providerMetadata": plainImmutableValue(llmResult.providerMetadata),
                    "observerErrors": list(llmResult.observerErrors),
                },
            )
            transaction.set(
                f"processing/{memoryKey}/lastrun",
                {"processingRunId": run.processingRunId},
            )

            run.enterStage(ProcessingStage.FINALIZE)
            finalizeResult = None
            if finalizeCapabilityId is not None:
                finalizeResult = self._capabilityInvoker(
                    finalizeCapabilityId,
                    self._finalizePayload(
                        run=run,
                        inputValue=inputValue,
                        finalizeInput=finalizeInput,
                        reusableItems=reusableItems,
                        acceptedItems=acceptedItems,
                        llmResult=llmResult,
                    ),
                )

            transaction.commit()
            committed = True
            run.complete()
            self._emitTrace("processing-run-completed", run, {"queryItemCount": len(acceptedItems)})
            return LlmProcessingResult(
                processingRunId=run.processingRunId,
                queryItems=acceptedItems,
                reusableQueryItems=reusableItems,
                llm=llmResult,
                finalizeResult=finalizeResult,
            )
        except Exception:
            run.fail()
            if not committed:
                try:
                    transaction.abort()
                except RuntimeError:
                    pass
            self._emitTrace("processing-run-failed", run, {})
            raise

    def _loadCurrentQueryItems(
        self,
        *,
        transaction: CommittedValueTransaction,
        memoryKey: str,
        currentItemsAddress: str,
    ) -> list[dict[str, object]]:
        currentIds = transaction.load(currentItemsAddress)
        if currentIds is MISSING:
            return []
        if not isinstance(currentIds, list) or not all(type(itemId) is str for itemId in currentIds):
            raise RuntimeError(f"Committed QueryItem index at {currentItemsAddress!r} is invalid.")

        snapshots: list[dict[str, object]] = []
        for itemId in currentIds:
            address = self._queryItemAddress(memoryKey, itemId)
            snapshot = transaction.load(address)
            if not isinstance(snapshot, dict):
                raise RuntimeError(f"Committed QueryItem {itemId!r} is missing or invalid.")
            if snapshot.get("itemId") != itemId:
                raise RuntimeError(f"Committed QueryItem identity mismatch at {address!r}.")
            snapshots.append(snapshot)
        return snapshots

    def _stageReusableQueryItems(
        self,
        transaction: CommittedValueTransaction,
        *,
        memoryKey: str,
        items: tuple[QueryItem, ...],
    ) -> None:
        for item in items:
            address = self._queryItemAddress(memoryKey, item.itemId)
            snapshot = item.snapshot()
            existing = transaction.load(address)
            if existing is MISSING:
                transaction.set(address, snapshot)
                continue
            if existing != snapshot:
                raise RuntimeError(
                    f"QueryItem identity {item.itemId!r} resolved to different content within processing memory.",
                )

    @staticmethod
    def _queryItemAddress(memoryKey: str, itemId: str) -> str:
        digest = hashlib.sha256(itemId.encode("utf-8")).hexdigest()
        return f"processing/{memoryKey}/items/{digest}"

    @staticmethod
    def _queryEvidence(query: LlmQuery) -> dict[str, object]:
        evidence: dict[str, object] = {
            "formatId": query.formatId,
            "metadata": plainImmutableValue(query.metadata),
            "payloadType": type(query.payload).__qualname__,
        }
        if type(query.payload) is str:
            encoded = query.payload.encode("utf-8")
            evidence["payloadBytes"] = len(encoded)
            evidence["payloadSha256"] = hashlib.sha256(encoded).hexdigest()
        elif type(query.payload) is bytes:
            evidence["payloadBytes"] = len(query.payload)
            evidence["payloadSha256"] = hashlib.sha256(query.payload).hexdigest()
        return evidence

    @staticmethod
    def _finalizePayload(
        *,
        run: ProcessingRun,
        inputValue: object,
        finalizeInput: object | None,
        reusableItems: tuple[QueryItem, ...],
        acceptedItems: tuple[QueryItem, ...],
        llmResult: StreamingLlmResult,
    ) -> dict[str, object]:
        return {
            "processingRunId": run.processingRunId,
            "input": inputValue,
            "finalizeInput": finalizeInput,
            "reusableQueryItems": [item.snapshot() for item in reusableItems],
            "queryItems": [item.snapshot() for item in acceptedItems],
            "llm": {
                "providerName": llmResult.providerName,
                "providerOwnerId": llmResult.providerOwnerId,
                "model": llmResult.model,
                "providerOptions": plainImmutableValue(llmResult.providerOptions),
                "executionProfile": {
                    "contextWindowTokens": llmResult.executionProfile.contextWindowTokens,
                    "metadata": plainImmutableValue(llmResult.executionProfile.metadata),
                },
                "providerMetadata": plainImmutableValue(llmResult.providerMetadata),
                "observerErrors": list(llmResult.observerErrors),
                "query": {
                    "formatId": llmResult.query.formatId,
                    "payload": llmResult.query.payload,
                    "metadata": plainImmutableValue(llmResult.query.metadata),
                },
                "response": {"rawText": llmResult.rawText},
            },
        }

    def _resolveExecution(
        self,
        *,
        providerName: str,
        model: str | None,
        providerOptions: Mapping[str, ImmutableValue] | None,
    ) -> tuple[
        Registration[LlmStreamProvider],
        LlmStreamProvider,
        Mapping[str, ImmutableValue],
        LlmExecutionProfile,
    ]:
        registration = self._providers.requireRegistration(providerName)
        provider = registration.value
        options = ImmutableValueFreezer().freezeMapping(providerOptions, "providerOptions")
        profile = provider.getExecutionProfile(model=model, providerOptions=options)
        if not isinstance(profile, LlmExecutionProfile):
            raise LlmProviderProtocolError("Provider getExecutionProfile() returned an invalid value.")
        return registration, provider, options, profile

    def _streamResolved(
        self,
        *,
        registration: Registration[LlmStreamProvider],
        provider: LlmStreamProvider,
        profile: LlmExecutionProfile,
        options: Mapping[str, ImmutableValue],
        query: LlmQuery,
        model: str | None,
        streamObserver: Callable[[LlmStreamEvent], None] | None,
        processingRun: ProcessingRun | None = None,
    ) -> StreamingLlmResult:
        request = LlmCallRequest(query=query, model=model, providerOptions=options)
        parts: list[str] = []
        completed = False
        finalMetadata: Mapping[str, ImmutableValue] = {}
        observerErrors: list[str] = []
        for index, event in enumerate(provider.stream(request)):
            if not isinstance(event, LlmStreamEvent):
                raise LlmProviderProtocolError(f"Provider yielded non-LlmStreamEvent at index {index}.")
            if completed:
                raise LlmProviderProtocolError("Provider emitted an event after completion.")
            if processingRun is not None:
                processingRun.enterStage(ProcessingStage.STREAM_EVENT)
            if event.eventType == "delta":
                parts.append(event.text)
            elif event.eventType == "completed":
                completed = True
                finalMetadata = event.metadata
            else:
                raise LlmProviderProtocolError(f"Unsupported provider event {event.eventType!r}.")
            if streamObserver is not None:
                try:
                    streamObserver(event)
                except Exception as err:
                    observerErrors.append(f"{type(err).__qualname__}: {err}")
                    self._emitObserverFailure(processingRun, err)
        if not completed:
            raise LlmProviderProtocolError("Provider stream ended without a completed event.")
        if processingRun is not None:
            processingRun.enterStage(ProcessingStage.PARSE_RESPONSE)
        return StreamingLlmResult(
            query=request.query,
            model=request.model,
            providerName=registration.name,
            providerOwnerId=registration.ownerId,
            providerOptions=request.providerOptions,
            executionProfile=profile,
            rawText="".join(parts),
            providerMetadata=finalMetadata,
            observerErrors=tuple(observerErrors),
        )

    @staticmethod
    def _requireQueryItems(value: object, *, stage: str) -> tuple[QueryItem, ...]:
        if not isinstance(value, (list, tuple)):
            raise TypeError(f"{stage} must return a list or tuple of QueryItems/snapshots.")
        items: list[QueryItem] = []
        identities: set[str] = set()
        for entry in value:
            item = entry if isinstance(entry, QueryItem) else QueryItem.fromSnapshot(entry)
            if item.itemId in identities:
                raise ValueError(f"{stage} returned duplicate QueryItem identity {item.itemId!r}.")
            identities.add(item.itemId)
            items.append(item)
        return tuple(items)

    @staticmethod
    def _requireFilteredSubset(source: tuple[QueryItem, ...], filtered: tuple[QueryItem, ...]) -> None:
        sourceById = {item.itemId: item for item in source}
        for item in filtered:
            original = sourceById.get(item.itemId)
            if original is None:
                raise ValueError(f"FILTER_QUERY_ITEMS introduced unknown QueryItem {item.itemId!r}.")
            if original != item:
                raise ValueError(f"FILTER_QUERY_ITEMS modified QueryItem {item.itemId!r}; filtering may only select items.")

    @staticmethod
    def _requireQuery(value: object) -> LlmQuery:
        if isinstance(value, LlmQuery):
            return value
        if not isinstance(value, dict):
            raise TypeError("BUILD_QUERY must return an LlmQuery or query snapshot object.")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, dict):
            raise TypeError("Built query metadata must be an object.")
        return LlmQuery(formatId=value.get("formatId"), payload=value.get("payload"), metadata=metadata)

    def _emitObserverFailure(self, run: ProcessingRun | None, err: Exception) -> None:
        if self._trace is None:
            return
        attributes: dict[str, object] = {"errorType": type(err).__qualname__, "message": str(err)}
        if run is not None:
            attributes["processingRunId"] = run.processingRunId
        try:
            self._trace("stream-observer-failed", attributes)
        except Exception:
            return

    def _emitTrace(self, reason: str, run: ProcessingRun, extra: dict[str, object]) -> None:
        if self._trace is None:
            return
        try:
            self._trace(
                reason,
                {
                    "processingRunId": run.processingRunId,
                    "pipelineId": run.pipelineId,
                    "stage": run.stage.value,
                    **extra,
                },
            )
        except Exception:
            return


StreamingLlmPipeline = LlmProcessingPipeline
