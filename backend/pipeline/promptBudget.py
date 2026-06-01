# backend/pipeline/promptBudget.py
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math


class PromptBudgetMode(StrEnum):
    """Supported prompt budget modes for Milestone 2 chat requests."""

    NONE = "none"
    ESTIMATED = "estimated"


class PromptBudgetExceededError(ValueError):
    """Raised when required prompt content cannot fit the configured prompt budget."""


@dataclass(frozen=True)
class PromptBudgetConfig:
    """Configuration for fitting model messages into a prompt token budget."""

    mode: PromptBudgetMode = PromptBudgetMode.NONE
    contextSize: int | None = None
    maxCompletionTokens: int = 512
    safetyMarginTokens: int = 128
    estimatedCharactersPerToken: float = 3.0


@dataclass(frozen=True)
class PromptBudgetResult:
    """Result of applying a prompt budget policy to model messages."""

    messages: list[dict[str, str]]
    mode: PromptBudgetMode
    wasTrimmed: bool = False
    droppedMessageCount: int = 0
    estimatedPromptTokens: int | None = None
    promptTokenBudget: int | None = None
    tokenCountSource: str | None = None


class PromptBudgetPolicy:
    """Fits OpenAI-style chat messages into a configured prompt budget."""

    def __init__(self, config: PromptBudgetConfig | None = None) -> None:
        self.config = config or PromptBudgetConfig()

    def fitMessages(self, messages: list[dict[str, str]]) -> PromptBudgetResult:
        copiedMessages = [dict(message) for message in messages]
        if self.config.mode == PromptBudgetMode.NONE:
            return PromptBudgetResult(
                messages=copiedMessages,
                mode=self.config.mode,
            )

        promptTokenBudget = self._getPromptTokenBudget()
        if promptTokenBudget is None:
            return PromptBudgetResult(
                messages=copiedMessages,
                mode=PromptBudgetMode.NONE,
            )

        return self._fitEstimatedMessages(copiedMessages, promptTokenBudget)

    def _getPromptTokenBudget(self) -> int | None:
        if self.config.contextSize is None or self.config.contextSize <= 0:
            return None

        promptTokenBudget = self.config.contextSize - self.config.maxCompletionTokens - self.config.safetyMarginTokens
        if promptTokenBudget <= 0:
            raise PromptBudgetExceededError(
                "configured prompt token budget is not positive: "
                f"context_size={self.config.contextSize}, "
                f"max_completion_tokens={self.config.maxCompletionTokens}, "
                f"safety_margin_tokens={self.config.safetyMarginTokens}"
            )

        return promptTokenBudget

    def _fitEstimatedMessages(
        self,
        messages: list[dict[str, str]],
        promptTokenBudget: int,
    ) -> PromptBudgetResult:
        if not messages:
            return PromptBudgetResult(
                messages=[],
                mode=self.config.mode,
                estimatedPromptTokens=0,
                promptTokenBudget=promptTokenBudget,
                tokenCountSource="estimated",
            )

        requiredMessage = messages[-1]
        requiredMessageTokens = self._estimateMessagesTokens([requiredMessage])
        if requiredMessageTokens > promptTokenBudget:
            raise PromptBudgetExceededError(
                "newest message is too large for the configured prompt token budget: "
                f"estimated_prompt_tokens={requiredMessageTokens}, "
                f"prompt_token_budget={promptTokenBudget}"
            )

        keptMessages: list[dict[str, str]] = [requiredMessage]
        keptPromptTokens = requiredMessageTokens
        droppedMessageCount = 0

        for message in reversed(messages[:-1]):
            candidateTokens = self._estimateMessagesTokens([message])
            if keptPromptTokens + candidateTokens > promptTokenBudget:
                droppedMessageCount += 1
                continue

            keptMessages.insert(0, message)
            keptPromptTokens += candidateTokens

        return PromptBudgetResult(
            messages=keptMessages,
            mode=self.config.mode,
            wasTrimmed=droppedMessageCount > 0,
            droppedMessageCount=droppedMessageCount,
            estimatedPromptTokens=keptPromptTokens,
            promptTokenBudget=promptTokenBudget,
            tokenCountSource="estimated",
        )

    def _estimateMessagesTokens(self, messages: list[dict[str, str]]) -> int:
        characterCount = 0
        for message in messages:
            characterCount += len(str(message.get("role", "")))
            characterCount += len(str(message.get("content", "")))
            characterCount += 8

        charactersPerToken = self.config.estimatedCharactersPerToken
        if charactersPerToken <= 0.0:
            charactersPerToken = 3.0

        return max(1, math.ceil(characterCount / charactersPerToken))
