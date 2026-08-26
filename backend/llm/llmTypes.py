# file: backend/llm/llmTypes.py ; version: 4
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer
from backend.core.validation import (
    requireExactNonBlankString,
    requireInstance,
    requireNonNegativeInteger,
    requirePositiveInteger,
    requireString,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__: list[str] = [
    "LlmCallRequest",
    "LlmExecutionProfile",
    "LlmQuery",
    "LlmQueryBudget",
    "LlmStreamEvent",
    "LlmStreamEventType",
    "LlmStreamObserver",
    "LlmStreamProvider",
    "LlmTokenEstimator",
]


type LlmStreamEventType = Literal[
    "delta",
    "completed",
]


@dataclass(frozen=True, slots=True)
class LlmQuery:
    """
    Represents one complete provider-neutral input for an LLM inference.

    formatId identifies the semantic representation contract of payload.
    Examples may include plain text, structured messages, multimodal content,
    references to Value System material, or another provider-neutral
    representation defined by an LLM component.

    The generic pipeline treats payload as opaque. It does not copy, freeze,
    serialize, normalize, or otherwise interpret the representation.

    LlmQuery itself is immutable, but payload is not recursively frozen by this
    class. The producer of a query is responsible for ensuring that the
    observable meaning of payload remains stable after LlmQuery construction.
    In particular, mutable aliases must not be used to change the representation
    while the query, a provider request containing it, or a processing result
    containing it remains observable.

    The query builder and provider adapter communicate through the semantic
    representation contract identified by formatId. A provider adapter may
    support only a subset of query formats and may reject unsupported formats
    through its provider-domain request contract.

    metadata contains immutable generic auxiliary information. It is available
    to framework infrastructure such as diagnostics and tracing without
    requiring that infrastructure to understand payload.
    """

    formatId: str
    payload: object
    metadata: Mapping[str, ImmutableValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validates the query representation and freezes generic metadata."""
        requireExactNonBlankString(self.formatId, "formatId")

        frozenMetadata = ImmutableValueFreezer().freezeMapping(
            self.metadata,
            "metadata",
        )
        object.__setattr__(self, "metadata", frozenMetadata)


@dataclass(frozen=True, slots=True)
class LlmQueryBudget:
    """
    Defines the effective input-token budget for one LLM inference.

    maxInputTokens is the final query-side token limit after provider and model
    context-window constraints have been applied.

    reservedResponseTokens records the amount of the provider/model context
    window reserved for generated output when the effective input budget was
    resolved. It is not a subset of maxInputTokens; the two fields describe
    different portions of context-window accounting.
    """

    maxInputTokens: int
    reservedResponseTokens: int = 0

    def __post_init__(self) -> None:
        """Validates the effective query-budget values."""
        requirePositiveInteger(self.maxInputTokens, "maxInputTokens")
        requireNonNegativeInteger(
            self.reservedResponseTokens,
            "reservedResponseTokens",
        )


class LlmTokenEstimator(Protocol):
    """
    Estimates provider/model input-token usage for complete LLM queries.

    An estimator interprets the complete LlmQuery representation rather than
    individual QueryItems. This lets token accounting include representation
    overhead such as message framing, multimodal structure, or other
    model-specific input encoding.
    """

    def estimateInputTokens(self, query: LlmQuery) -> int:
        """
        Returns the estimated input-token count for query.

        Implementations must return an exact built-in non-negative integer.
        The caller validates this runtime contract before using the estimate.
        """
        ...


@dataclass(frozen=True, slots=True)
class LlmExecutionProfile:
    """
    Describes provider/model execution constraints known before query building.

    contextWindowTokens is the complete provider/model context-window size when
    known.

    tokenEstimator supplies representation-aware input-token accounting for the
    selected provider/model. None requests use of the pipeline's configured
    fallback estimator.

    metadata contains immutable provider/model information that may be retained
    for generic diagnostics, tracing, or execution inspection.
    """

    contextWindowTokens: int | None = None
    tokenEstimator: LlmTokenEstimator | None = None
    metadata: Mapping[str, ImmutableValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validates and freezes the execution-profile values."""
        if self.contextWindowTokens is not None:
            requirePositiveInteger(
                self.contextWindowTokens,
                "contextWindowTokens",
            )

        if self.tokenEstimator is not None and not callable(
            getattr(self.tokenEstimator, "estimateInputTokens", None),
        ):
            raise TypeError(
                "tokenEstimator must expose callable "
                "estimateInputTokens(query).",
            )

        frozenMetadata = ImmutableValueFreezer().freezeMapping(
            self.metadata,
            "metadata",
        )
        object.__setattr__(self, "metadata", frozenMetadata)


@dataclass(frozen=True, slots=True)
class LlmCallRequest:
    """
    Represents one immutable request passed to an LLM provider adapter.

    query contains the provider-neutral semantic inference input.

    model optionally selects the provider/model execution target.

    providerOptions contains immutable provider-specific execution
    configuration. Provider-specific options are deliberately kept outside the
    provider-neutral LlmQuery representation.
    """

    query: LlmQuery
    model: str | None = None
    providerOptions: Mapping[str, ImmutableValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validates and freezes the provider-facing call request."""
        requireInstance(self.query, LlmQuery, "query")

        if self.model is not None:
            requireExactNonBlankString(self.model, "model")

        frozenProviderOptions = ImmutableValueFreezer().freezeMapping(
            self.providerOptions,
            "providerOptions",
        )
        object.__setattr__(self, "providerOptions", frozenProviderOptions)


@dataclass(frozen=True, slots=True)
class LlmStreamEvent:
    """
    Represents one immutable provider-neutral text-output stream event.

    A delta event carries one text fragment and may carry immutable auxiliary
    metadata.

    A completed event terminates the provider stream. It carries no text but may
    carry final immutable metadata such as usage or provider/model information.

    This type intentionally models text-output streaming only. Other future LLM
    output modalities or structured event kinds require explicit contracts
    rather than being encoded implicitly into text or metadata.
    """

    eventType: LlmStreamEventType
    text: str = ""
    metadata: Mapping[str, ImmutableValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validates and freezes the provider stream event."""
        requireString(self.eventType, "eventType")

        if self.eventType not in {"delta", "completed"}:
            raise ValueError(
                f"Unsupported LLM stream event type {self.eventType!r}.",
            )

        requireString(self.text, "text")

        if self.eventType == "completed" and self.text:
            raise ValueError(
                "A completed LLM stream event must not contain text.",
            )

        frozenMetadata = ImmutableValueFreezer().freezeMapping(
            self.metadata,
            "metadata",
        )
        object.__setattr__(self, "metadata", frozenMetadata)


class LlmStreamObserver(Protocol):
    """
    Observes processed text-output stream events emitted by the pipeline.

    The observer receives pipeline-processed events rather than the raw events
    yielded directly by the provider. Observation is an external side effect;
    receiving an event does not imply that surrounding Value System state has
    been committed.
    """

    def __call__(self, event: LlmStreamEvent) -> None:
        """Observes one immutable processed stream event."""
        ...


class LlmStreamProvider(Protocol):
    """
    Defines the provider-adapter contract used by LlmProcessingPipeline.

    A provider adapter receives a complete provider-neutral LlmQuery inside an
    LlmCallRequest. It is responsible for interpreting the query formats it
    supports and translating those representations to its concrete provider API.

    getExecutionProfile() runs before query filtering and construction so the
    pipeline can resolve model/provider context constraints and token-estimation
    behaviour before choosing QueryItems.

    stream() yields provider-neutral text-output events. The stream must
    eventually yield exactly one completed event and must not yield further
    events after completion. LlmProcessingPipeline enforces that stream
    lifecycle contract.
    """

    def getExecutionProfile(
        self,
        *,
        model: str | None,
        providerOptions: Mapping[str, ImmutableValue],
    ) -> LlmExecutionProfile:
        """Returns provider/model constraints required before query construction."""
        ...

    def stream(self, request: LlmCallRequest) -> Iterator[LlmStreamEvent]:
        """Streams provider-neutral text-output events for one call request."""
        ...
