# file: backend/llm/llmTypes.py ; version: 2
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer
from backend.core.validation import requireExactNonBlankString, requireInteger, requireNonBlankString, requireString

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__: list[str] = [
    "LlmCallRequest",
    "LlmExecutionProfile",
    "LlmPromptBudget",
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
class LlmPromptBudget:
    """Defines the effective token allocation for one prompt."""

    maxPromptTokens: int
    reservedResponseTokens: int = 0

    def __post_init__(self) -> None:
        """Validates the prompt-budget values."""
        requireInteger(self.maxPromptTokens, "maxPromptTokens")
        if self.maxPromptTokens < 1:
            raise ValueError("maxPromptTokens must be greater than zero.")

        requireInteger(self.reservedResponseTokens, "reservedResponseTokens")
        if self.reservedResponseTokens < 0:
            raise ValueError("reservedResponseTokens must not be negative.")


class LlmTokenEstimator(Protocol):
    """Estimates token usage for one provider/model profile."""

    def estimateTokens(self, text: str) -> int:
        """Returns the estimated non-negative token count for text."""
        ...


@dataclass(frozen=True, slots=True)
class LlmExecutionProfile:
    """Describes provider/model constraints available before filtering."""

    contextWindowTokens: int | None = None
    tokenEstimator: LlmTokenEstimator | None = None
    metadata: Mapping[str, ImmutableValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validates and freezes the execution-profile values."""
        if self.contextWindowTokens is not None:
            requireInteger(self.contextWindowTokens, "contextWindowTokens")

            if self.contextWindowTokens < 1:
                raise ValueError(
                    "contextWindowTokens must be greater than zero.",
                )

        if self.tokenEstimator is not None and not callable(
            getattr(self.tokenEstimator, "estimateTokens", None),
        ):
            raise TypeError(
                "tokenEstimator must expose callable estimateTokens(text).",
            )

        frozenMetadata = ImmutableValueFreezer().freezeMapping(
            self.metadata,
            "metadata",
        )
        object.__setattr__(self, "metadata", frozenMetadata)


@dataclass(frozen=True, slots=True)
class LlmCallRequest:
    """Represents an immutable provider-facing LLM call request."""

    prompt: str
    model: str | None = None
    providerOptions: Mapping[str, ImmutableValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validates and freezes the provider-facing call request."""
        requireNonBlankString(self.prompt, "prompt")

        if self.model is not None:
            requireExactNonBlankString(self.model, "model")

        frozenProviderOptions = ImmutableValueFreezer().freezeMapping(
            self.providerOptions,
            "providerOptions",
        )
        object.__setattr__(self, "providerOptions", frozenProviderOptions)


@dataclass(frozen=True, slots=True)
class LlmStreamEvent:
    """Represents one immutable provider-neutral stream event."""

    eventType: LlmStreamEventType
    text: str = ""
    metadata: Mapping[str, ImmutableValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validates and freezes the provider stream event."""
        if self.eventType not in {"delta", "completed"}:
            raise ValueError(
                f"Unsupported LLM stream event type {self.eventType!r}.",
            )

        requireString(self.text, "text")

        if self.eventType == "completed" and self.text:
            raise ValueError(
                "A completed event must not contain text.",
            )

        frozenMetadata = ImmutableValueFreezer().freezeMapping(
            self.metadata,
            "metadata",
        )
        object.__setattr__(self, "metadata", frozenMetadata)


class LlmStreamObserver(Protocol):
    """Receives immutable processed stream events from the pipeline."""

    def __call__(self, event: LlmStreamEvent) -> None:
        """Observes one processed stream event."""
        ...


class LlmStreamProvider(Protocol):
    """
    Defines the provider contract used by LlmProcessingPipeline.

    Implementations may live in backend code or activated driver Packs. The
    provider receives an LlmCallRequest and emits LlmStreamEvent values.
    """

    def getExecutionProfile(
        self,
        *,
        model: str | None,
        providerOptions: Mapping[str, ImmutableValue],
    ) -> LlmExecutionProfile:
        """Returns provider/model constraints before prompt filtering."""
        ...

    def stream(self, request: LlmCallRequest) -> Iterator[LlmStreamEvent]:
        """Streams events for one provider-facing LLM call."""
        ...
