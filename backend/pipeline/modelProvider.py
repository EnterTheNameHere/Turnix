# backend/pipeline/modelProvider.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class ModelCompletionOutcome(StrEnum):
    """Turnix-facing classification for valid model completion results."""

    COMPLETE = "complete"
    PARTIAL_CONTENT_HIT_TOKEN_LIMIT = "partial_content_hit_token_limit"
    NO_VISIBLE_CONTENT_HIT_TOKEN_LIMIT = "no_visible_content_hit_token_limit"
    EMPTY_RESPONSE = "empty_response"


@dataclass(frozen=True)
class ModelUsage:
    """Token usage reported by a model provider when available."""

    promptTokens: int | None = None
    completionTokens: int | None = None
    totalTokens: int | None = None
    cachedPromptTokens: int | None = None


@dataclass(frozen=True)
class ModelTimings:
    """Timing information for one model provider call when available."""

    wallMilliseconds: float | None = None
    promptMilliseconds: float | None = None
    predictedMilliseconds: float | None = None
    predictedTokensPerSecond: float | None = None


@dataclass(frozen=True)
class ModelResponse:
    """Validated Turnix-facing model completion response."""

    content: str
    finishReason: str | None = None
    reasoningContent: str = ""
    model: str | None = None
    usage: ModelUsage = field(default_factory=ModelUsage)
    timings: ModelTimings = field(default_factory=ModelTimings)
    providerDetails: dict[str, Any] = field(default_factory=dict)

    @property
    def hasVisibleContent(self) -> bool:
        return bool(self.content.strip())

    @property
    def hasReasoningContent(self) -> bool:
        return bool(self.reasoningContent.strip())

    @property
    def reachedTokenLimit(self) -> bool:
        return self.finishReason == "length"

    def classifyOutcome(self) -> ModelCompletionOutcome:
        if self.hasVisibleContent and self.reachedTokenLimit:
            return ModelCompletionOutcome.PARTIAL_CONTENT_HIT_TOKEN_LIMIT

        if self.hasVisibleContent:
            return ModelCompletionOutcome.COMPLETE

        if self.reachedTokenLimit:
            return ModelCompletionOutcome.NO_VISIBLE_CONTENT_HIT_TOKEN_LIMIT

        return ModelCompletionOutcome.EMPTY_RESPONSE


class ModelProvider(Protocol):
    """Boundary for chat model providers used by ChatPipeline."""

    def generateChatResponse(self, messages: list[dict[str, str]]) -> ModelResponse:
        """Returns one assistant response for the supplied OpenAI-style messages."""
        ...

class MockModelProvider:
    """Small deterministic provider used until the llama.cpp server provider is wired in."""

    def generateChatResponse(self, messages: list[dict[str, str]]) -> ModelResponse:
        lastUser = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                lastUser = message.get("content", "")
                break

        if not lastUser:
            return ModelResponse(
                content="Mock response: no user message was provided.",
                finishReason="stop",
                model="mock",
            )

        return ModelResponse(
            content=f"Mock response: {lastUser}",
            finishReason="stop",
            model="mock",
        )
