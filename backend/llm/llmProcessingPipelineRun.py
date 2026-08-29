# file: backend/llm/llmProcessingPipelineRun.py ; version: 8
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.core.ids import Uuid7Id
from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer
from backend.core.validation import (
    requireExactNonBlankString,
    requireInstance,
    requireOptionalInstance,
    requireString,
    typeName,
)
from backend.llm.errors import LlmPipelineStateError
from backend.llm.llmOwner import LlmBackendOwner, LlmOwner, requireLlmOwner
from backend.llm.llmQuery import (
    LlmQueryItem,
    LlmQueryItemId,
    LlmQueryItemIdentity,
    validateBuiltLlmQuery,
)
from backend.llm.llmQueryFilter import (
    LlmQueryItemFilterContext,
    LlmQueryItemFilterResult,
    validateLlmQueryItemFilterResult,
)
from backend.llm.llmTypes import (
    LlmCallRequest,
    LlmExecutionProfile,
    LlmQuery,
    LlmQueryBudget,
    LlmStreamEvent,
    LlmStreamObserver,
    LlmTokenEstimator,
)
from backend.pack.packCodeEntry import PackCodeEntryInstanceId
from backend.values import ValueLayer

if TYPE_CHECKING:
    from collections.abc import Mapping

    from backend.values import ValueAddress


__all__: list[str] = [
    "LlmProcessingRequest",
    "LlmProcessingRun",
    "LlmProcessingRunId",
    "LlmProcessingRunResult",
    "LlmResponse",
]


class LlmProcessingRunId(Uuid7Id):
    """Identifies one LLM processing run."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class LlmProcessingRequest:
    """
    Requests one dynamically constructed LLM processing run.

    purposeId identifies the semantic purpose of the inference independently of
    the selected provider or model.

    providerName selects one registered LLM provider.

    inputData contains immutable caller-supplied input available to processing
    hooks and components. It is not itself the final LLM query.

    model optionally selects a provider-specific model.

    providerOptions contains immutable provider-specific execution
    configuration.

    queryBudget optionally constrains query input and response reservation.
    Provider/model limits may further reduce the effective input budget used by
    the run.

    streamObserver optionally receives processed text-output stream events as
    they escape the pipeline. Observation is an external side effect and does
    not imply that surrounding Value System state has been committed.
    """

    purposeId: str
    providerName: str
    inputData: Mapping[str, ImmutableValue] = field(default_factory=dict)
    model: str | None = None
    providerOptions: Mapping[str, ImmutableValue] = field(default_factory=dict)
    queryBudget: LlmQueryBudget | None = None
    streamObserver: LlmStreamObserver | None = None

    def __post_init__(self) -> None:
        """Validates and freezes the processing-request data."""
        requireExactNonBlankString(self.purposeId, "purposeId")
        requireExactNonBlankString(self.providerName, "providerName")

        frozenInputData = ImmutableValueFreezer().freezeMapping(
            self.inputData,
            "inputData",
        )
        object.__setattr__(self, "inputData", frozenInputData)

        if self.model is not None:
            requireExactNonBlankString(self.model, "model")

        frozenProviderOptions = ImmutableValueFreezer().freezeMapping(
            self.providerOptions,
            "providerOptions",
        )
        object.__setattr__(self, "providerOptions", frozenProviderOptions)

        requireOptionalInstance(
            self.queryBudget,
            LlmQueryBudget,
            "queryBudget",
        )

        if (
            self.streamObserver is not None
            and not callable(self.streamObserver)
        ):
            raise TypeError(
                "streamObserver must be callable; "
                f"received {typeName(self.streamObserver)}.",
            )


@dataclass(frozen=True, slots=True)
class LlmResponse:
    """
    Represents provider text output and final pipeline-processed response data.

    rawText contains provider delta text before STREAM_EVENT stage processing.

    processedText contains the final response after stream-event processing and
    response parsing.

    providerMetadata contains immutable metadata supplied by the provider's
    completed stream event.

    responseData contains immutable owner-namespaced auxiliary values produced
    by LLM processing components. It is separate from provider metadata and
    from authoritative Value System state.
    """

    rawText: str
    processedText: str
    providerMetadata: Mapping[str, ImmutableValue]
    responseData: Mapping[str, ImmutableValue]

    def __post_init__(self) -> None:
        """Validates text output and freezes response metadata."""
        requireString(self.rawText, "rawText")
        requireString(self.processedText, "processedText")

        freezer = ImmutableValueFreezer()
        object.__setattr__(
            self,
            "providerMetadata",
            freezer.freezeMapping(self.providerMetadata, "providerMetadata"),
        )
        object.__setattr__(
            self,
            "responseData",
            freezer.freezeMapping(self.responseData, "responseData"),
        )


@dataclass(frozen=True, slots=True)
class LlmProcessingRunResult:
    """
    Represents the successful externally retained result of one processing run.

    runId identifies the completed run.

    callRequest is the final request actually supplied to the provider after
    provider-call preparation hooks.

    response contains raw provider text, processed text, provider metadata, and
    auxiliary response data accumulated during processing.

    Successful construction of this result means that the LLM processing
    pipeline completed successfully. It does not imply that a caller-supplied
    ValueTransaction or any surrounding authoritative state transition has been
    committed.
    """

    runId: LlmProcessingRunId
    callRequest: LlmCallRequest
    response: LlmResponse

    def __post_init__(self) -> None:
        """Validates the completed processing-run result."""
        requireInstance(self.runId, LlmProcessingRunId, "runId")
        requireInstance(self.callRequest, LlmCallRequest, "callRequest")
        requireInstance(self.response, LlmResponse, "response")


@dataclass(slots=True)
class LlmProcessingRun:
    """
    Holds backend-owned mutable transient state for one active LLM run.

    This object is the pipeline's mutable execution record. It is not an
    extension-component capability surface. Pack hooks receive LlmStageContext,
    which exposes only operations valid for the active stage.

    Constructor fields describe the immutable or externally supplied conditions
    under which the run executes. Lifecycle state such as candidate QueryItems,
    filtering results, built queries, provider-call state, stream buffers, and
    response data is always initialized internally and cannot be injected
    through the constructor.

    Query processing advances conceptually through:

        candidate QueryItems
            -> filterResult
            -> currentQuery
            -> callRequest
            -> provider stream
            -> processed response

    Mutation methods enforce the ordering invariants they can establish
    locally. Stage-specific permissions remain the responsibility of
    LlmStageContext and LlmProcessingPipeline.

    values is an optional caller-owned Value System resolution view. The
    pipeline never commits or aborts it. If values is a ValueTransaction,
    writes performed through the run remain staged until the surrounding
    controller or orchestration scope explicitly commits or aborts that
    transaction.
    """

    runId: LlmProcessingRunId
    purposeId: str
    providerName: str
    providerOwnerId: PackCodeEntryInstanceId
    inputData: Mapping[str, ImmutableValue]
    model: str | None
    providerOptions: Mapping[str, ImmutableValue]
    executionProfile: LlmExecutionProfile
    budget: LlmQueryBudget
    tokenEstimator: LlmTokenEstimator
    streamObserver: LlmStreamObserver | None
    values: ValueLayer | None = None

    queryItems: list[LlmQueryItem] = field(default_factory=list, init=False)
    filterResult: LlmQueryItemFilterResult | None = field(
        default=None,
        init=False,
    )
    currentQuery: LlmQuery | None = field(default=None, init=False)
    callRequest: LlmCallRequest | None = field(default=None, init=False)

    currentStreamEvent: LlmStreamEvent | None = field(default=None, init=False)
    currentStreamText: str = field(default="", init=False)
    currentStreamSuppressed: bool = field(default=False, init=False)
    rawResponseParts: list[str] = field(default_factory=list, init=False)
    processedResponseParts: list[str] = field(default_factory=list, init=False)
    processedResponse: str | None = field(default=None, init=False)
    providerMetadata: Mapping[str, ImmutableValue] = field(
        default_factory=dict,
        init=False,
    )
    _responseData: dict[str, ImmutableValue] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        """Validates and freezes the externally supplied initial run state."""
        requireInstance(self.runId, LlmProcessingRunId, "runId")
        requireExactNonBlankString(self.purposeId, "purposeId")
        requireExactNonBlankString(self.providerName, "providerName")
        requireInstance(
            self.providerOwnerId,
            PackCodeEntryInstanceId,
            "providerOwnerId",
        )

        self.inputData = ImmutableValueFreezer().freezeMapping(
            self.inputData,
            "inputData",
        )

        if self.model is not None:
            requireExactNonBlankString(self.model, "model")

        self.providerOptions = ImmutableValueFreezer().freezeMapping(
            self.providerOptions,
            "providerOptions",
        )

        requireInstance(
            self.executionProfile,
            LlmExecutionProfile,
            "executionProfile",
        )
        requireInstance(self.budget, LlmQueryBudget, "budget")

        if not callable(
            getattr(self.tokenEstimator, "estimateInputTokens", None),
        ):
            raise TypeError(
                "tokenEstimator must expose callable "
                "estimateInputTokens(query); "
                f"received {typeName(self.tokenEstimator)}.",
            )

        if (
            self.streamObserver is not None
            and not callable(self.streamObserver)
        ):
            raise TypeError(
                "streamObserver must be callable; "
                f"received {typeName(self.streamObserver)}.",
            )

        requireOptionalInstance(self.values, ValueLayer, "values")

        self.providerMetadata = ImmutableValueFreezer().freezeMapping(
            self.providerMetadata,
            "providerMetadata",
        )

    def addQueryItem(
        self,
        *,
        ownerId: LlmOwner,
        itemId: LlmQueryItemId,
        contentType: str,
        payload: object,
        importance: int,
        mandatory: bool,
        estimatedInputTokens: int | None,
        category: str | None,
        metadata: Mapping[str, object],
    ) -> LlmQueryItemIdentity:
        """
        Adds one owned transient candidate QueryItem to this run.

        QueryItems may be added only before a filter result has been accepted.
        Once filtering has produced a selection, the candidate universe is
        closed for the remainder of the run.

        Args:
            ownerId:
                Backend or Pack owner contributing the QueryItem.
            itemId:
                Identifier unique within that owner's QueryItems for this run.
            contentType:
                Semantic representation identifier for payload.
            payload:
                Opaque run-local QueryItem payload.
            importance:
                Relative filtering preference.
            mandatory:
                Whether filtering must retain the item.
            estimatedInputTokens:
                Optional advisory item-local token estimate.
            category:
                Optional semantic category.
            metadata:
                Generic metadata to freeze onto the QueryItem.

        Returns:
            The complete producer-qualified identity assigned to the new item.

        Raises:
            LlmPipelineStateError:
                If filtering has already established a candidate selection.
            ValueError:
                If the resulting identity duplicates an existing candidate.
            TypeError:
                If an argument violates its runtime contract.

        """
        if self.filterResult is not None:
            raise LlmPipelineStateError(
                "QueryItems cannot be added after query filtering has "
                "produced a result.",
            )

        requireLlmOwner(ownerId, "ownerId")
        requireInstance(itemId, LlmQueryItemId, "itemId")

        identity = LlmQueryItemIdentity(ownerId=ownerId, itemId=itemId)
        if any(item.identity == identity for item in self.queryItems):
            raise ValueError(
                "Duplicate LLM query item identity; "
                f"ownerId={ownerId!r}, "
                f"itemId={itemId!r}.",
            )

        item = LlmQueryItem(
            identity=identity,
            contentType=contentType,
            payload=payload,
            importance=importance,
            mandatory=mandatory,
            estimatedInputTokens=estimatedInputTokens,
            category=category,
            metadata=ImmutableValueFreezer().freezeMapping(
                metadata,
                "metadata",
            ),
        )

        self.queryItems.append(item)
        return identity

    def setFilterResult(
        self,
        result: LlmQueryItemFilterResult,
        *,
        context: LlmQueryItemFilterContext,
    ) -> None:
        """
        Validates and accepts one complete QueryItem filtering result.

        The filtering context must describe this run's current candidate
        universe in the same order and with the original candidate instances.
        Its effective budget must equal this run's effective budget.

        A filter result may be replaced while the filtering stage is active,
        allowing an after-filter hook to replace the central filter result.
        Once query construction has produced currentQuery, the selection is
        closed and cannot be replaced.

        Raises:
            LlmPipelineStateError:
                If query construction has already begun or context does not
                describe this run's candidate universe and effective budget.
            LlmQueryBudgetError:
                If the selected representation exceeds the effective budget.
            TypeError:
                If result or context violates its runtime contract.

        """
        if self.currentQuery is not None:
            raise LlmPipelineStateError(
                "The query-item filter result cannot change after query "
                "construction has produced a query.",
            )

        requireInstance(context, LlmQueryItemFilterContext, "context")
        self._validateFilterContextForRun(context)

        validateLlmQueryItemFilterResult(result=result, context=context)
        self.filterResult = result

    def setQuery(self, query: LlmQuery) -> None:
        """
        Validates and accepts the complete query built from selected
        QueryItems.

        A valid filter result must exist before a query can be accepted.
        BUILD_QUERY stage after-hooks may replace the query before
        provider-call preparation begins.

        The complete built representation is always validated against the
        effective input-token budget; selection-stage estimates do not replace
        this check.

        Raises:
            LlmPipelineStateError:
                If no filter result exists or provider-call preparation has
                already produced a call request.
            LlmQueryBudgetError:
                If query exceeds the effective input-token budget.
            TypeError:
                If query or an associated estimator result violates its runtime
                contract.

        """
        if self.filterResult is None:
            raise LlmPipelineStateError(
                "A query cannot be accepted before query filtering has "
                "produced a result.",
            )

        if self.callRequest is not None:
            raise LlmPipelineStateError(
                "The built query cannot change after provider-call "
                "preparation has produced a call request.",
            )

        validateBuiltLlmQuery(
            query=query,
            budget=self.budget,
            tokenEstimator=self.tokenEstimator,
        )
        self.currentQuery = query

    def loadValue(self, address: str | ValueAddress) -> object:
        """
        Loads one addressed value through the caller-supplied Value System
        view.

        The concrete ValueLayer determines visibility and fallback semantics.

        Raises:
            RuntimeError:
                If this processing run has no Value System view.

        """
        values = self._requireValues()
        return values.value(address).load()

    def setValue(self, address: str | ValueAddress, value: object) -> None:
        """
        Mutates one value through the caller-supplied Value System view.

        The concrete view determines mutation semantics. A ValueTransaction
        stages the mutation. A layer that does not permit direct mutation
        rejects the operation according to its own contract.

        This method never commits authoritative state.

        Raises:
            RuntimeError:
                If this processing run has no Value System view.

        """
        values = self._requireValues()
        values.value(address).set(value)

    def setResponseData(
        self,
        *,
        ownerId: LlmOwner,
        key: str,
        value: object,
    ) -> None:
        """
        Adds one immutable owner-namespaced response-data value.

        Each owner receives an independent namespace. A key may be written at
        most once by that owner during the run. Existing response data is never
        silently overwritten.

        Response data is auxiliary processing output. Writing it does not
        mutate or commit the caller-supplied Value System view.

        Args:
            ownerId:
                Backend or Pack owner producing the response-data value.
            key:
                Nonblank owner-local response-data key.
            value:
                Value to recursively freeze before publication.

        Raises:
            ValueError:
                If the owner-qualified key already exists.
            TypeError:
                If ownerId, key, or value violates its runtime contract.

        """
        requireLlmOwner(ownerId, "ownerId")

        cleanKey = requireExactNonBlankString(key, "key")
        fullKey = f"{_ownerNamespace(ownerId)}:{cleanKey}"

        if fullKey in self._responseData:
            raise ValueError(f"Response data key {fullKey!r} already exists.")

        frozenValue = ImmutableValueFreezer().freeze(
            value,
            f"responseData.{fullKey}",
        )

        self._responseData[fullKey] = frozenValue

    def responseDataSnapshot(self) -> Mapping[str, ImmutableValue]:
        """
        Returns an immutable snapshot of accumulated auxiliary response data.

        Later additions to this run's response data do not mutate the returned
        snapshot.
        """
        return ImmutableValueFreezer().freezeMapping(
            self._responseData,
            "responseData",
        )

    def _validateFilterContextForRun(
        self,
        context: LlmQueryItemFilterContext,
    ) -> None:
        """
        Validates that a filtering context belongs to this run's current state.

        Candidate comparison uses object identity rather than QueryItem
        equality because QueryItem payloads are opaque and may define
        arbitrary equality behaviour.
        """
        if context.budget != self.budget:
            raise LlmPipelineStateError(
                "The query-item filtering context does not use this run's "
                "effective query budget.",
            )

        if len(context.queryItems) != len(self.queryItems):
            raise LlmPipelineStateError(
                "The query-item filtering context does not describe this "
                "run's complete candidate universe.",
            )

        for index, (contextItem, runItem) in enumerate(
            zip(context.queryItems, self.queryItems, strict=True),
        ):
            if contextItem is not runItem:
                raise LlmPipelineStateError(
                    "The query-item filtering context does not contain this "
                    f"run's original candidate QueryItem at index {index}.",
                )

    def _requireValues(self) -> ValueLayer:
        """
        Returns the caller-supplied Value System view.

        Raises:
            RuntimeError:
                If no Value System view was supplied for this processing run.

        """
        if self.values is None:
            raise RuntimeError(
                "This LLM processing run has no Value System view.",
            )

        return self.values


def _ownerNamespace(ownerId: LlmOwner) -> str:
    """
    Returns the stable type-qualified response-data namespace for one owner.

    The owner kind is encoded explicitly so built-in backend ownership and Pack
    ownership cannot collide even if their printable identifiers happen to
    contain identical text.
    """
    requireLlmOwner(ownerId, "ownerId")

    if isinstance(ownerId, LlmBackendOwner):
        return f"backend:{ownerId.ownerId}"

    if isinstance(ownerId, PackCodeEntryInstanceId):
        return f"pack:{ownerId.value}"

    raise AssertionError(
        "requireLlmOwner() accepted an unsupported LLM owner type.",
    )
