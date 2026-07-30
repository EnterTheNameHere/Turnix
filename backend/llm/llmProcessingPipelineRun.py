# file: backend/llm/llmProcessingRun.py ; version: 5
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.core.ids import Uuid7Id
from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer
from backend.core.validation import requireExactNonBlankString, requireInteger, requireString, typeName
from backend.llm.errors import LlmPipelineStateError, LlmPromptBudgetError
from backend.llm.llmPrompt import LlmProcessedInput, LlmProcessedInputId
from backend.llm.llmQueryItem import LlmQueryItem, LlmQueryItemFilterResult, LlmQueryItemId, LlmQueryItemIdentity
from backend.llm.llmTypes import (
    LlmCallRequest,
    LlmExecutionProfile,
    LlmPromptBudget,
    LlmStreamEvent,
    LlmStreamObserver,
    LlmTokenEstimator,
)
from backend.pack.packCodeEntry import PackCodeEntryInstanceId

if TYPE_CHECKING:
    from collections.abc import Mapping


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
    """Requests one dynamically constructed LLM processing run."""

    purposeId: str
    providerName: str
    rawInput: object
    model: str | None = None
    providerOptions: Mapping[str, ImmutableValue] = field(default_factory=dict)
    promptBudget: LlmPromptBudget | None = None
    streamObserver: LlmStreamObserver | None = None

    def __post_init__(self) -> None:
        """Validates and freezes the LLM processing request."""
        requireExactNonBlankString(self.purposeId, "purposeId")
        requireExactNonBlankString(self.providerName, "providerName")

        if self.model is not None:
            requireExactNonBlankString(self.model, "model")

        frozenProviderOptions = ImmutableValueFreezer().freezeMapping(
            self.providerOptions,
            "providerOptions",
        )
        object.__setattr__(self, "providerOptions", frozenProviderOptions)

        if self.promptBudget is not None and not isinstance(
            self.promptBudget,
            LlmPromptBudget,
        ):
            raise TypeError(
                "promptBudget must be an LlmPromptBudget; "
                f"got {typeName(self.promptBudget)}.",
            )
        if self.streamObserver is not None and not callable(
            self.streamObserver,
        ):
            raise TypeError(
                "streamObserver must be a callable; "
                f"got {typeName(self.streamObserver)}.",
            )


@dataclass(frozen=True, slots=True)
class LlmResponse:
    """Raw provider output and final pipeline-processed response."""

    rawText: str
    processedText: str
    providerMetadata: Mapping[str, ImmutableValue]
    responseData: Mapping[str, ImmutableValue]

    def __post_init__(self) -> None:
        """Validates and freezes the LLM response."""
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
    """Result returned after the complete pipeline transaction commits."""

    runId: LlmProcessingRunId
    callRequest: LlmCallRequest
    response: LlmResponse

    def __post_init__(self) -> None:
        """Validates the LLM processing-run result."""
        if not isinstance(self.runId, LlmProcessingRunId):
            raise TypeError(
                "runId must be an LlmProcessingRunId; "
                f"got {typeName(self.runId)}.",
            )

        if not isinstance(self.callRequest, LlmCallRequest):
            raise TypeError(
                "callRequest must be an LlmCallRequest; "
                f"got {typeName(self.callRequest)}.",
            )

        if not isinstance(self.response, LlmResponse):
            raise TypeError(
                "response must be an LlmResponse; "
                f"got {typeName(self.response)}.",
            )


@dataclass(slots=True)
class LlmProcessingRun:
    """Mutable internal state of one active pipeline run."""

    runId: LlmProcessingRunId
    purposeId: str
    providerName: str
    rawInput: Mapping[str, ImmutableValue]
    model: str | None
    providerOptions: Mapping[str, ImmutableValue]
    executionProfile: LlmExecutionProfile
    budget: LlmPromptBudget
    tokenEstimator: LlmTokenEstimator
    streamObserver: LlmStreamObserver | None

    processedInput: list[LlmProcessedInput] = field(default_factory=list)
    queryItems: list[LlmQueryItem] = field(default_factory=list)
    selectedQueryItems: list[LlmQueryItem] = field(default_factory=list)
    excludedQueryItems: list[LlmQueryItem] = field(default_factory=list)
    currentPrompt: str = ""
    callRequest: LlmCallRequest | None = None

    currentStreamEvent: LlmStreamEvent | None = None
    currentStreamText: str = ""
    currentStreamSuppressed: bool = False
    rawResponseParts: list[str] = field(default_factory=list)
    processedResponseParts: list[str] = field(default_factory=list)
    processedResponse: str = ""
    providerMetadata: Mapping[str, ImmutableValue] = field(default_factory=dict)
    _responseData: dict[str, ImmutableValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validates and freezes the initial LLM processing-run state."""
        if not isinstance(self.runId, LlmProcessingRunId):
            raise TypeError(
                "runId must be an LlmProcessingRunId; "
                f"got {typeName(self.runId)}.",
            )

        requireExactNonBlankString(self.purposeId, "purposeId")
        requireExactNonBlankString(self.providerName, "providerName")

        frozenRawInput = ImmutableValueFreezer().freezeMapping(
            self.rawInput,
            "rawInput",
        )
        self.rawInput = frozenRawInput

        if self.model is not None:
            requireExactNonBlankString(self.model, "model")

        frozenProviderOptions = ImmutableValueFreezer().freezeMapping(
            self.providerOptions,
            "providerOptions",
        )
        self.providerOptions = frozenProviderOptions

        if not isinstance(self.executionProfile, LlmExecutionProfile):
            raise TypeError(
                "executionProfile must be an LlmExecutionProfile; "
                f"got {typeName(self.executionProfile)}.",
            )

        if not isinstance(self.budget, LlmPromptBudget):
            raise TypeError(
                "budget must be an LlmPromptBudget; "
                f"got {typeName(self.budget)}.",
            )

        if not callable(getattr(self.tokenEstimator, "estimateTokens", None)):
            raise TypeError(
                "tokenEstimator must expose a callable estimateTokens(text); "
                f"got {typeName(self.tokenEstimator)}.",
            )

        if self.streamObserver is not None and not callable(
            self.streamObserver,
        ):
            raise TypeError(
                "streamObserver must be callable; "
                f"got {typeName(self.streamObserver)}.",
            )

        frozenProviderMetadata = ImmutableValueFreezer().freezeMapping(
            self.providerMetadata,
            "providerMetadata",
        )
        self.providerMetadata = frozenProviderMetadata

    def addProcessedInput(
        self,
        *,
        ownerId: PackCodeEntryInstanceId,
        inputId: LlmProcessedInputId,
        value: object,
    ) -> None:
        """Adds one owned processed-input contribution."""
        if not isinstance(ownerId, PackCodeEntryInstanceId):
            raise TypeError(
                "ownerId must be a PackCodeEntryInstanceId; "
                f"got {typeName(ownerId)}.",
            )

        if not isinstance(inputId, LlmProcessedInputId):
            raise TypeError(
                "inputId must be an LlmProcessedInputId; "
                f"got {typeName(inputId)}.",
            )

        identity = (ownerId, inputId)

        if any(
            (item.ownerId, item.inputId) == identity
            for item in self.processedInput
        ):
            raise ValueError(
                "Duplicate processed-input identity: "
                f"ownerId={ownerId!r}, "
                f"inputId={inputId!r}.",
            )

        frozenValue = ImmutableValueFreezer().freeze(
            value,
            f"processedInput[{ownerId.value!r}][{inputId.value!r}]",
        )
        self.processedInput.append(
            LlmProcessedInput(
                ownerId=ownerId,
                inputId=inputId,
                value=frozenValue,
            ),
        )

    def addQueryItem(
        self,
        *,
        ownerId: PackCodeEntryInstanceId,
        itemId: LlmQueryItemId,
        content: str,
        importance: int,
        mandatory: bool,
        estimatedTokens: int | None,
        category: str | None,
        metadata: Mapping[str, object],
    ) -> LlmQueryItemIdentity:
        """Adds one owned candidate query item."""
        identity = LlmQueryItemIdentity(
            ownerId=ownerId,
            itemId=itemId,
        )
        if any(item.identity == identity for item in self.queryItems):
            raise ValueError(
                "Duplicate LLM query item identity: "
                f"ownerId={ownerId!r}, itemId={itemId!r}.",
            )

        frozenMetadata = ImmutableValueFreezer().freezeMapping(
            metadata,
            "metadata",
        )

        item = LlmQueryItem(
            identity=identity,
            content=content,
            importance=importance,
            mandatory=mandatory,
            estimatedTokens=estimatedTokens,
            category=category,
            metadata=frozenMetadata,
        )
        self.queryItems.append(item)
        return identity

    def removeQueryItem(self, identity: LlmQueryItemIdentity) -> None:
        """Removes one query item and any current filter selection."""
        if not isinstance(identity, LlmQueryItemIdentity):
            raise TypeError(
                "identity must be an LlmQueryItemIdentity; "
                f"got {typeName(identity)}.",
            )

        for index, item in enumerate(self.queryItems):
            if item.identity == identity:
                del self.queryItems[index]
                self.selectedQueryItems = [
                    selected
                    for selected in self.selectedQueryItems
                    if selected.identity != identity
                ]
                self.excludedQueryItems = [
                    excluded
                    for excluded in self.excludedQueryItems
                    if excluded.identity != identity
                ]
                return
        raise KeyError(identity)

    def setFilterResult(self, result: LlmQueryItemFilterResult) -> None:
        """Validates and applies one query-item filter result."""
        if not isinstance(result, LlmQueryItemFilterResult):
            raise TypeError(
                "result must be an LlmQueryItemFilterResult; "
                f"got {typeName(result)}.",
            )

        originalItemsByIdentity = {
            item.identity: item
            for item in self.queryItems
        }
        resultItems = result.selectedItems + result.excludedItems

        for item in resultItems:
            originalItem = originalItemsByIdentity.get(item.identity)

            if originalItem is None:
                raise LlmPipelineStateError(
                    "The query-item filter returned an unknown query item; "
                    f"ownerId={item.identity.ownerId!r}, "
                    f"itemId={item.identity.itemId!r}.",
                )

            if item is not originalItem:
                raise LlmPipelineStateError(
                    "The query-item filter must return "
                    "the original query-item instances; "
                    f"ownerId={originalItem.identity.ownerId!r}, "
                    f"itemId={originalItem.identity.itemId!r}.",
                )

        resultIdentities = {
            item.identity
            for item in resultItems
        }
        originalIdentities = set(originalItemsByIdentity)

        if resultIdentities != originalIdentities:
            missingIdentities = originalIdentities - resultIdentities
            missingIdentity = min(
                missingIdentities,
                key=lambda identity: (
                    str(identity.ownerId),
                    identity.itemId.value,
                ),
            )
            raise LlmPipelineStateError(
                "The query-item filter omitted a candidate query item; "
                f"ownerId={missingIdentity.ownerId!r}, "
                f"itemId={missingIdentity.itemId!r}.",
            )

        selectedIdentities = {
            item.identity
            for item in result.selectedItems
        }

        for item in self.queryItems:
            if item.mandatory and item.identity not in selectedIdentities:
                raise LlmPipelineStateError(
                    "The query-item filter excluded a mandatory query item; "
                    f"ownerId={item.identity.ownerId!r}, "
                    f"itemId={item.identity.itemId!r}.",
                )

        expectedSelectedItems = tuple(
            item
            for item in self.queryItems
            if item.identity in selectedIdentities
        )
        expectedExcludedItems = tuple(
            item
            for item in self.queryItems
            if item.identity not in selectedIdentities
        )

        if result.selectedItems != expectedSelectedItems:
            raise LlmPipelineStateError(
                "The query-item filter changed the relative "
                "order of selected query items.",
            )

        if result.excludedItems != expectedExcludedItems:
            raise LlmPipelineStateError(
                "The query-item filter changed the relative "
                "order of excluded query items.",
            )

        selectedContent = "\n\n".join(
            item.content
            for item in result.selectedItems
        )
        estimatedTokens = self.tokenEstimator.estimateTokens(selectedContent)

        try:
            estimatedTokens = requireInteger(
                estimatedTokens,
                "LlmTokenEstimator estimate",
            )
        except TypeError as err:
            raise LlmPipelineStateError(
                f"LlmTokenEstimator returned a non-integer estimate; "
                f"got {typeName(estimatedTokens)}.",
            ) from err

        if estimatedTokens < 0:
            raise LlmPipelineStateError(
                f"LlmTokenEstimator returned a negative estimate; "
                f"got {estimatedTokens}.",
            )

        if estimatedTokens > self.budget.maxPromptTokens:
            raise LlmPromptBudgetError(
                "Selected query items exceed the prompt budget; "
                f"estimatedTokens={estimatedTokens}, "
                f"maxPromptTokens={self.budget.maxPromptTokens}.",
            )

        self.selectedQueryItems = list(result.selectedItems)
        self.excludedQueryItems = list(result.excludedItems)

    def setResponseData(
        self,
        *,
        ownerId: PackCodeEntryInstanceId,
        key: str,
        value: object,
    ) -> None:
        """Adds one namespaced immutable response-data value."""
        if not isinstance(ownerId, PackCodeEntryInstanceId):
            raise TypeError(
                "ownerId must be a PackCodeEntryInstanceId; "
                f"got {typeName(ownerId)}.",
            )

        cleanKey = requireExactNonBlankString(key, "key")
        fullKey = f"{ownerId.value}:{cleanKey}"
        if fullKey in self._responseData:
            raise ValueError(f"Response data key {fullKey!r} already exists.")

        self._responseData[fullKey] = ImmutableValueFreezer().freeze(
            value,
            f"responseData.{fullKey}",
        )

    def responseDataSnapshot(self) -> Mapping[str, ImmutableValue]:
        """Returns an immutable snapshot of accumulated response data."""
        return ImmutableValueFreezer().freezeMapping(
            self._responseData,
            "responseData",
        )
