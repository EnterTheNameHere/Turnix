from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.core.immutableValue import ImmutableValue
from backend.llm.errors import LlmProviderProtocolError
from backend.llm.llmTypes import LlmCallRequest, LlmExecutionProfile, LlmQuery, LlmStreamEvent, LlmStreamProvider
from backend.processing.runtime import ProcessingRun, ProcessingStage, QueryItem, plainImmutableValue
from backend.registration import Registration, RegistrationRegistry, RegistrationScope
from backend.values.sentinels import MISSING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from backend.values.committed import CommittedValueLayer

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


@dataclass(frozen=True, slots=True)
class LlmProcessingResult:
    """Completed ProcessingRun plus streamed provider evidence."""

    processingRunId: str
    queryItems: tuple[QueryItem, ...]
    llm: StreamingLlmResult


class LlmProcessingPipeline:
    """Reusable staged LLM pipeline backed by ApplicationRun committed memory.

    Pack behaviour participates through named capabilities. Each stage invocation
    therefore receives a fresh CodeEntry Context through RuntimeHost rather than
    retaining the Context that requested the pipeline run.
    """

    def __init__(
        self,
        *,
        providers: LlmProviderRegistry,
        state: CommittedValueLayer,
        capabilityInvoker: Callable[[str, object | None], object],
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
        """Executes the provider streaming portion directly for non-staged callers."""
        return self._stream(
            providerName=providerName,
            query=query,
            model=model,
            providerOptions=providerOptions,
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
        streamObserver: Callable[[LlmStreamEvent], None] | None = None,
    ) -> LlmProcessingResult:
        if type(memoryKey) is not str or not memoryKey or not memoryKey.replace("-", "").replace("_", "").isalnum() or not memoryKey.islower():
            raise ValueError("memoryKey must be a lowercase Value-address-safe identifier.")

        transaction = self._state.openTransaction()
        run = ProcessingRun(pipelineId=f"llm:{memoryKey}", transaction=transaction)
        queryItemsAddress = f"processing/{memoryKey}/queryitems"
        try:
            previous = transaction.load(queryItemsAddress)
            previousSnapshots = [] if previous is MISSING else previous
            if not isinstance(previousSnapshots, list):
                raise RuntimeError(f"Committed QueryItem memory at {queryItemsAddress!r} is invalid.")

            run.enterStage(ProcessingStage.BUILD_QUERY_ITEMS)
            built = self._capabilityInvoker(
                buildQueryItemsCapabilityId,
                {"input": inputValue, "previousQueryItems": previousSnapshots},
            )
            items = self._requireQueryItems(built, stage="BUILD_QUERY_ITEMS")

            if filterQueryItemsCapabilityId is not None:
                run.enterStage(ProcessingStage.FILTER_QUERY_ITEMS)
                filtered = self._capabilityInvoker(
                    filterQueryItemsCapabilityId,
                    {"input": inputValue, "queryItems": [item.snapshot() for item in items]},
                )
                items = self._requireQueryItems(filtered, stage="FILTER_QUERY_ITEMS")
            run.queryItems = items

            run.enterStage(ProcessingStage.BUILD_QUERY)
            builtQuery = self._capabilityInvoker(
                buildQueryCapabilityId,
                {"input": inputValue, "queryItems": [item.snapshot() for item in items]},
            )
            query = self._requireQuery(builtQuery)

            run.enterStage(ProcessingStage.PREPARE_PROVIDER_CALL)
            llmResult = self._stream(
                providerName=providerName,
                query=query,
                model=model,
                providerOptions=providerOptions,
                streamObserver=streamObserver,
                processingRun=run,
            )

            run.enterStage(ProcessingStage.UPDATE_QUERY_ITEMS)
            queryItemSnapshots = [item.snapshot() for item in items]
            transaction.set(queryItemsAddress, queryItemSnapshots)
            transaction.set(
                f"processing/{memoryKey}/runs/{run.processingRunId}",
                {
                    "processingRunId": run.processingRunId,
                    "queryItems": queryItemSnapshots,
                    "query": {
                        "formatId": llmResult.query.formatId,
                        "payload": llmResult.query.payload,
                        "metadata": plainImmutableValue(llmResult.query.metadata),
                    },
                    "response": {"rawText": llmResult.rawText},
                    "provider": {
                        "name": llmResult.providerName,
                        "ownerId": llmResult.providerOwnerId,
                        "model": llmResult.model,
                        "options": plainImmutableValue(llmResult.providerOptions),
                        "metadata": plainImmutableValue(llmResult.providerMetadata),
                    },
                },
            )
            transaction.set(
                f"processing/{memoryKey}/lastrun",
                {"processingRunId": run.processingRunId},
            )

            run.enterStage(ProcessingStage.FINALIZE)
            transaction.commit()
            run.complete()
            self._emitTrace("processing-run-completed", run, {"queryItemCount": len(items)})
            return LlmProcessingResult(processingRunId=run.processingRunId, queryItems=items, llm=llmResult)
        except Exception:
            run.fail()
            try:
                transaction.abort()
            except RuntimeError:
                pass
            self._emitTrace("processing-run-failed", run, {})
            raise

    def _stream(
        self,
        *,
        providerName: str,
        query: LlmQuery,
        model: str | None,
        providerOptions: Mapping[str, ImmutableValue] | None,
        streamObserver: Callable[[LlmStreamEvent], None] | None,
        processingRun: ProcessingRun | None = None,
    ) -> StreamingLlmResult:
        registration = self._providers.requireRegistration(providerName)
        provider = registration.value
        options = {} if providerOptions is None else providerOptions
        request = LlmCallRequest(query=query, model=model, providerOptions=options)
        profile = provider.getExecutionProfile(model=request.model, providerOptions=request.providerOptions)
        if not isinstance(profile, LlmExecutionProfile):
            raise LlmProviderProtocolError("Provider getExecutionProfile() returned an invalid value.")

        parts: list[str] = []
        completed = False
        finalMetadata: Mapping[str, ImmutableValue] = {}
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
                streamObserver(event)
        if not completed:
            raise LlmProviderProtocolError("Provider stream ended without a completed event.")
        if processingRun is not None:
            processingRun.enterStage(ProcessingStage.PARSE_RESPONSE)
        return StreamingLlmResult(
            query=request.query,
            model=request.model,
            providerName=providerName,
            providerOwnerId=registration.ownerId,
            providerOptions=request.providerOptions,
            executionProfile=profile,
            rawText="".join(parts),
            providerMetadata=finalMetadata,
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
    def _requireQuery(value: object) -> LlmQuery:
        if isinstance(value, LlmQuery):
            return value
        if not isinstance(value, dict):
            raise TypeError("BUILD_QUERY must return an LlmQuery or query snapshot object.")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, dict):
            raise TypeError("Built query metadata must be an object.")
        return LlmQuery(formatId=value.get("formatId"), payload=value.get("payload"), metadata=metadata)

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
            # Trace evidence must not decide processing-state correctness.
            return


StreamingLlmPipeline = LlmProcessingPipeline
