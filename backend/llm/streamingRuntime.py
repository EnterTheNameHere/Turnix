# file: backend/llm/streamingRuntime.py ; version: 7
from __future__ import annotations

import hashlib
import time
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
    "LlmProcessingPreview",
    "LlmProcessingResult",
    "LlmProviderRegistry",
    "StreamingLlmPipeline",
    "StreamingLlmResult",
]


class LlmProviderRegistry:
    """Published registry of provider-neutral streaming LLM providers."""

    def __init__(self) -> None:
        """Creates an empty provider registry."""
        self._registry: RegistrationRegistry[LlmStreamProvider] = RegistrationRegistry()

    def register(self, scope: RegistrationScope, *, ownerId: str, name: str, provider: LlmStreamProvider) -> None:
        """Stages one provider registration in the supplied registration scope."""
        if not callable(getattr(provider, "stream", None)) or not callable(getattr(provider, "getExecutionProfile", None)):
            raise TypeError("provider must satisfy the LlmStreamProvider contract.")
        scope.register(self._registry, ownerId=ownerId, name=name, value=provider)

    def requireRegistration(self, name: str) -> Registration[LlmStreamProvider]:
        """Returns the published provider registration for a logical name."""
        return self._registry.require(name)

    def require(self, name: str) -> LlmStreamProvider:
        """Returns the published provider implementation for a logical name."""
        return self.requireRegistration(name).value

    def unregisterOwnedBy(self, ownerId: str) -> None:
        """Removes all published providers owned by one CodeEntry instance."""
        self._registry.unregisterOwnedBy(ownerId)


@dataclass(frozen=True, slots=True)
class StreamingLlmResult:
    """Completed provider call with exact response and generic execution timing."""

    query: LlmQuery
    model: str | None
    providerName: str
    providerOwnerId: str
    providerOptions: Mapping[str, ImmutableValue]
    executionProfile: LlmExecutionProfile
    rawText: str
    startedTimeNs: int
    endedTimeNs: int
    durationNs: int
    providerMetadata: Mapping[str, ImmutableValue] = field(default_factory=dict)
    observerErrors: tuple[str, ...] = ()

    def timingSnapshot(self) -> dict[str, int]:
        """Returns JSON-compatible timing evidence for the provider execution span."""
        return {
            "startedTimeNs": self.startedTimeNs,
            "endedTimeNs": self.endedTimeNs,
            "durationNs": self.durationNs,
        }


@dataclass(frozen=True, slots=True)
class LlmProcessingPreview:
    """Prepared model-facing processing state without an EngineCall."""

    queryItems: tuple[QueryItem, ...]
    reusableQueryItems: tuple[QueryItem, ...]
    query: LlmQuery
    providerName: str
    providerOwnerId: str
    providerOptions: Mapping[str, ImmutableValue]
    executionProfile: LlmExecutionProfile
    inputTokens: int | None


@dataclass(frozen=True, slots=True)
class LlmProcessingResult:
    """Completed ProcessingRun plus streamed provider and transactional completion evidence."""

    processingRunId: str
    queryItems: tuple[QueryItem, ...]
    reusableQueryItems: tuple[QueryItem, ...]
    llm: StreamingLlmResult
    completionResult: object | None = None


class LlmProcessingPipeline:
    """Reusable staged LLM pipeline with optional ApplicationRun committed memory.

    Reusable QueryItems are committed individually and referenced by identity so
    overlapping ProcessingRuns do not repeatedly commit the same large source
    material. Filtering affects only the accepted query for the current run; it
    does not erase reusable items prepared by BUILD_QUERY_ITEMS.

    Optional application completion executes inside the ProcessingRun
    transaction after model evidence has been staged. Completion may derive and
    stage additional persistent application state, but must not publish export
    files or other irreversible external effects. Only the outermost commit
    makes ProcessingRun and completion state authoritative together.

    Export is intentionally outside this pipeline. Applications may publish
    projections only after runProcessing() returns successfully.
    """

    def __init__(
        self,
        *,
        providers: LlmProviderRegistry,
        state: CommittedValueLayer | None = None,
        capabilityInvoker: Callable[[str, object | None, CommittedValueTransaction | None], object] | None = None,
        trace: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        """Binds provider, optional state, capability, and tracing dependencies."""
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
        """Runs one provider call without creating persistent ProcessingRun state."""
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

    def prepareProcessing(
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
        memoryView: CommittedValueLayer | CommittedValueTransaction | None = None,
    ) -> LlmProcessingPreview:
        """Prepares the exact model-facing query without invoking the provider stream.

        Reusable preparation is accepted into the supplied transaction base so
        source/semantic materialization performed for preview can be reused by a
        later real ProcessingRun. No ProcessingRun record, response evidence, or
        completion result is created because no EngineCall occurred.
        """
        if self._state is None or self._capabilityInvoker is None:
            raise RuntimeError("prepareProcessing() requires committed state and a capability invoker.")
        if type(memoryKey) is not str or not memoryKey or not memoryKey.replace("-", "").replace("_", "").isalnum() or not memoryKey.islower():
            raise ValueError("memoryKey must be a lowercase Value-address-safe identifier.")

        transactionBase = self._state if memoryView is None else memoryView
        transaction = transactionBase.openTransaction()
        committed = False
        try:
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
            currentItemsAddress = f"processing/{memoryKey}/currentqueryitems"
            previousSnapshots = self._loadCurrentQueryItems(
                transaction=transaction,
                memoryKey=memoryKey,
                currentItemsAddress=currentItemsAddress,
            )
            built = self._capabilityInvoker(
                buildQueryItemsCapabilityId,
                {
                    "input": inputValue,
                    "previousQueryItems": previousSnapshots,
                    "execution": executionSnapshot,
                },
                transaction,
            )
            reusableItems = self._requireQueryItems(built, stage="BUILD_QUERY_ITEMS")
            self._stageReusableQueryItems(transaction, memoryKey=memoryKey, items=reusableItems)

            acceptedItems = reusableItems
            if filterQueryItemsCapabilityId is not None:
                filtered = self._capabilityInvoker(
                    filterQueryItemsCapabilityId,
                    {
                        "input": inputValue,
                        "queryItems": [item.snapshot() for item in reusableItems],
                        "execution": executionSnapshot,
                    },
                    transaction,
                )
                acceptedItems = self._requireQueryItems(filtered, stage="FILTER_QUERY_ITEMS")
                self._requireFilteredSubset(reusableItems, acceptedItems)

            builtQuery = self._capabilityInvoker(
                buildQueryCapabilityId,
                {
                    "input": inputValue,
                    "queryItems": [item.snapshot() for item in acceptedItems],
                    "execution": executionSnapshot,
                },
                transaction,
            )
            query = self._requireQuery(builtQuery)
            inputTokens: int | None = None
            estimator = profile.tokenEstimator
            if estimator is not None:
                measured = estimator.estimateInputTokens(query)
                if type(measured) is not int or measured < 0:
                    raise LlmProviderProtocolError(
                        f"Provider token estimator returned an invalid value: {measured!r}.",
                    )
                inputTokens = measured

            transaction.set(currentItemsAddress, [item.itemId for item in reusableItems])
            transaction.commit()
            committed = True
            return LlmProcessingPreview(
                queryItems=acceptedItems,
                reusableQueryItems=reusableItems,
                query=query,
                providerName=providerName,
                providerOwnerId=registration.ownerId,
                providerOptions=options,
                executionProfile=profile,
                inputTokens=inputTokens,
            )
        finally:
            if not committed:
                try:
                    transaction.abort()
                except RuntimeError:
                    pass

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
        completionCapabilityId: str | None = None,
        completionInput: object | None = None,
        streamObserver: Callable[[LlmStreamEvent], None] | None = None,
        memoryView: CommittedValueLayer | CommittedValueTransaction | None = None,
    ) -> LlmProcessingResult:
        """Runs and transactionally persists one complete LLM ProcessingRun."""
        if self._state is None or self._capabilityInvoker is None:
            raise RuntimeError("runProcessing() requires committed state and a capability invoker.")
        if type(memoryKey) is not str or not memoryKey or not memoryKey.replace("-", "").replace("_", "").isalnum() or not memoryKey.islower():
            raise ValueError("memoryKey must be a lowercase Value-address-safe identifier.")

        transactionBase = self._state if memoryView is None else memoryView
        transaction = transactionBase.openTransaction()
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
                transaction,
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
                    transaction,
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
                transaction,
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
                    "acceptedQueryItems": [item.snapshot() for item in acceptedItems],
                    "query": self._queryEvidence(llmResult.query),
                    "response": {
                        "rawText": llmResult.rawText,
                        "utf8Bytes": len(llmResult.rawText.encode("utf-8")),
                        "sha256": hashlib.sha256(llmResult.rawText.encode("utf-8")).hexdigest(),
                    },
                    "execution": executionSnapshot,
                    "providerTiming": llmResult.timingSnapshot(),
                    "providerMetadata": plainImmutableValue(llmResult.providerMetadata),
                    "observerErrors": list(llmResult.observerErrors),
                },
            )
            transaction.set(f"processing/{memoryKey}/lastrun", {"processingRunId": run.processingRunId})

            run.enterStage(ProcessingStage.COMPLETE)
            completionResult = None
            if completionCapabilityId is not None:
                completionResult = self._capabilityInvoker(
                    completionCapabilityId,
                    self._completionPayload(
                        run=run,
                        inputValue=inputValue,
                        completionInput=completionInput,
                        reusableItems=reusableItems,
                        acceptedItems=acceptedItems,
                        llmResult=llmResult,
                    ),
                    transaction,
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
                completionResult=completionResult,
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
        """Loads the previous reusable QueryItem snapshots for one memory key."""
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
        """Stages changed reusable QueryItems under stable content-independent addresses."""
        for item in items:
            address = self._queryItemAddress(memoryKey, item.itemId)
            snapshot = item.snapshot()
            existing = transaction.load(address)
            if existing is MISSING or existing != snapshot:
                transaction.set(address, snapshot)

    @staticmethod
    def _queryItemAddress(memoryKey: str, itemId: str) -> str:
        """Returns the stable committed address for one reusable QueryItem identity."""
        digest = hashlib.sha256(itemId.encode("utf-8")).hexdigest()
        return f"processing/{memoryKey}/items/{digest}"

    @staticmethod
    def _queryEvidence(query: LlmQuery) -> dict[str, object]:
        """Returns persistent evidence sufficient to reconstruct the exact query.

        Human-facing exports are not the persistence source for model inputs.
        Text and byte payloads therefore retain their exact content here in
        authoritative ProcessingRun memory, alongside hashes useful for quick
        comparison and audit. Opaque provider payloads retain type/metadata
        evidence only until a provider-neutral persistence codec exists.
        """
        evidence: dict[str, object] = {
            "formatId": query.formatId,
            "metadata": plainImmutableValue(query.metadata),
            "payloadType": type(query.payload).__qualname__,
        }
        if type(query.payload) is str:
            encoded = query.payload.encode("utf-8")
            evidence["payload"] = query.payload
            evidence["payloadBytes"] = len(encoded)
            evidence["payloadSha256"] = hashlib.sha256(encoded).hexdigest()
        elif type(query.payload) is bytes:
            import base64

            evidence["payloadBase64"] = base64.b64encode(query.payload).decode("ascii")
            evidence["payloadBytes"] = len(query.payload)
            evidence["payloadSha256"] = hashlib.sha256(query.payload).hexdigest()
        return evidence

    @staticmethod
    def _completionPayload(
        *,
        run: ProcessingRun,
        inputValue: object,
        completionInput: object | None,
        reusableItems: tuple[QueryItem, ...],
        acceptedItems: tuple[QueryItem, ...],
        llmResult: StreamingLlmResult,
    ) -> dict[str, object]:
        """Builds completion input including exact provider evidence and timing."""
        return {
            "processingRunId": run.processingRunId,
            "input": inputValue,
            "completionInput": completionInput,
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
                "timing": llmResult.timingSnapshot(),
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
        """Resolves provider registration, immutable options, and execution profile."""
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
        """Consumes one provider stream and measures its complete execution span.

        The wall-clock endpoints and monotonic duration bracket provider.stream()
        creation through validation of its terminal completed event. Query
        construction, execution-profile resolution, ProcessingRun persistence,
        and completion handling are outside this timing boundary. Synchronous
        stream observers execute inside the boundary because they participate in
        consumption/backpressure of the provider stream.
        """
        request = LlmCallRequest(query=query, model=model, providerOptions=options)
        parts: list[str] = []
        completed = False
        finalMetadata: Mapping[str, ImmutableValue] = {}
        observerErrors: list[str] = []
        startedTimeNs = time.time_ns()
        startedMonotonicNs = time.monotonic_ns()
        stream = provider.stream(request)
        for index, event in enumerate(stream):
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
        endedMonotonicNs = time.monotonic_ns()
        endedTimeNs = time.time_ns()
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
            startedTimeNs=startedTimeNs,
            endedTimeNs=endedTimeNs,
            durationNs=endedMonotonicNs - startedMonotonicNs,
            providerMetadata=finalMetadata,
            observerErrors=tuple(observerErrors),
        )

    @staticmethod
    def _requireQueryItems(value: object, *, stage: str) -> tuple[QueryItem, ...]:
        """Validates a capability result as unique immutable QueryItems."""
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
        """Requires filtering to select unchanged source items without introducing data."""
        sourceById = {item.itemId: item for item in source}
        for item in filtered:
            original = sourceById.get(item.itemId)
            if original is None:
                raise ValueError(f"FILTER_QUERY_ITEMS introduced unknown QueryItem {item.itemId!r}.")
            if original != item:
                raise ValueError(f"FILTER_QUERY_ITEMS modified QueryItem {item.itemId!r}; filtering may only select items.")

    @staticmethod
    def _requireQuery(value: object) -> LlmQuery:
        """Normalizes a BUILD_QUERY result to the provider-neutral LlmQuery type."""
        if isinstance(value, LlmQuery):
            return value
        if not isinstance(value, dict):
            raise TypeError("BUILD_QUERY must return an LlmQuery or query snapshot object.")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, dict):
            raise TypeError("Built query metadata must be an object.")
        return LlmQuery(formatId=value.get("formatId"), payload=value.get("payload"), metadata=metadata)

    def _emitObserverFailure(self, run: ProcessingRun | None, err: Exception) -> None:
        """Emits non-authoritative observer-failure tracing without affecting inference."""
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
        """Emits best-effort ProcessingRun tracing isolated from execution semantics."""
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
