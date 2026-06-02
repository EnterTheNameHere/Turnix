# backend/pipeline/promptBudget.py
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class PromptTokenBudgetMode(StrEnum):
    """Supported prompt token budget modes for chat model requests."""

    NONE = "none"
    ESTIMATED = "estimated"


class PromptTokenBudgetExceededError(ValueError):
    """Raised when required prompt content cannot fit the configured prompt token budget."""

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}

    @classmethod
    def forNewestMessageTooLarge(
        cls,
        *,
        lastMessageTokenCount: int,
        promptTokenBudget: int,
        tokenCountSource: str,
    ) -> PromptTokenBudgetExceededError:
        message = (
            "newest message is too large for the configured prompt token budget: "
            f"last_message_token_count={lastMessageTokenCount}, "
            f"prompt_token_budget={promptTokenBudget}, "
            f"token_count_source={tokenCountSource}"
        )
        return cls(
            message,
            details={
                "reason": "newest_message_too_large",
                "lastMessageTokenCount": lastMessageTokenCount,
                "promptTokenBudget": promptTokenBudget,
                "tokenCountSource": tokenCountSource,
            },
        )

    @classmethod
    def forNoPromptTokensRemaining(
        cls,
        *,
        contextSize: int,
        reservedResponseTokenCount: int,
        safetyMarginTokenCount: int,
    ) -> PromptTokenBudgetExceededError:
        promptTokenBudget = contextSize - reservedResponseTokenCount - safetyMarginTokenCount
        message = (
            "prompt token budget is invalid because no prompt tokens remain: "
            f"context_size={contextSize}"
            f"reserved_response_token_count={reservedResponseTokenCount}, "
            f"safety_margin_token_count={safetyMarginTokenCount}, "
            f"prompt_token_budget={promptTokenBudget}"
        )
        return cls(
            message,
            details={
                "reason": "no_prompt_tokens_remaining",
                "contextSize": contextSize,
                "reservedResponseTokenCount": reservedResponseTokenCount,
                "safetyMarginTokenCount": safetyMarginTokenCount,
            },
        )


class PromptTokenCounter(Protocol):
    """Counts or estimates prompt tokens for OpenAI-style chat messages."""

    @property
    def tokenCountSource(self) -> str:
        """Human-readable source name for diagnostic output."""
        ...

    def countChatPromptTokens(self, messages: list[dict[str, str]]) -> int | None:
        """Returns token count when available, or None when this counter does not count."""
        ...


@dataclass(frozen=True)
class PromptTokenBudgetConfig:
    """Configuration for fitting model messages into a prompt token budget."""

    contextSize: int | None = None
    reservedResponseTokenCount: int = 512
    safetyMarginTokenCount: int = 128


@dataclass(frozen=True)
class PromptTokenBudgetTrimResult:
    """Result of applying a prompt token budget trimming policy to model messages."""

    keptMessages: list[dict[str, str]]
    wasTrimmed: bool = False
    usedPromptTokenCount: int | None = None
    droppedMessageCount: int = 0
    promptTokenBudget: int | None = None
    remainingPromptTokenBudget: int | None = None
    tokenCountSource: str | None = None


class NoPromptTokenCounter:
    """Token counter strategy that intentionally disables prompt budgeting."""

    @property
    def tokenCountSource(self) -> str:
        return "none"

    def countChatPromptTokens(self, messages: list[dict[str, str]]) -> int | None:
        return None


class EstimatedPromptTokenCounter:
    """Local heuristic token counter for cheap approximate prompt budgeting."""

    def __init__(self, *, estimatedCharactersPerToken: float = 3.0) -> None:
        self.estimatedCharactersPerToken = estimatedCharactersPerToken

    @property
    def tokenCountSource(self) -> str:
        return "estimated"

    def countChatPromptTokens(self, messages: list[dict[str, str]]) -> int:
        characterCount = 0
        for message in messages:
            characterCount += len(str(message.get("role", "")))
            characterCount += len(str(message.get("content", "")))
            characterCount += 8

        charactersPerToken = self.estimatedCharactersPerToken
        if charactersPerToken <= 0.0:
            charactersPerToken = 3.0

        return max(1, math.ceil(characterCount / charactersPerToken))


class PromptTokenBudgetPolicy:
    """Fits OpenAI-style chat messages into a configured prompt token budget."""

    def __init__(
        self,
        *,
        config: PromptTokenBudgetConfig | None = None,
        tokenCounter: PromptTokenCounter | None = None,
    ) -> None:
        self.config = config or PromptTokenBudgetConfig()
        self.tokenCounter = tokenCounter or NoPromptTokenCounter()

    def trimMessagesToBudget(self, messages: list[dict[str, str]]) -> PromptTokenBudgetTrimResult:
        if not messages:
            return PromptTokenBudgetTrimResult(keptMessages=[])

        messagesCopy = [dict(message) for message in messages]
        promptTokenBudget = self._getPromptTokenBudget()
        if promptTokenBudget is None:
            return PromptTokenBudgetTrimResult(keptMessages=messagesCopy)

        requiredLastMessage = messagesCopy[-1]
        lastMessageTokenCount = self.tokenCounter.countChatPromptTokens([requiredLastMessage])
        if lastMessageTokenCount is None:
            return PromptTokenBudgetTrimResult(keptMessages=messagesCopy)

        if lastMessageTokenCount > promptTokenBudget:
            raise PromptTokenBudgetExceededError.forNewestMessageTooLarge(
                lastMessageTokenCount=lastMessageTokenCount,
                promptTokenBudget=promptTokenBudget,
                tokenCountSource=self.tokenCounter.tokenCountSource,
            )

        keptMessages: list[dict[str, str]] = [requiredLastMessage]
        usedPromptTokenCount = lastMessageTokenCount

        olderMessages = messagesCopy[:-1]
        for message in reversed(olderMessages):
            candidateMessages = [message, *keptMessages]
            candidateTokenCount = self.tokenCounter.countChatPromptTokens(candidateMessages)
            if candidateTokenCount is None:
                return PromptTokenBudgetTrimResult(keptMessages=messagesCopy)

            if candidateTokenCount > promptTokenBudget:
                break

            keptMessages.insert(0, message)
            usedPromptTokenCount = candidateTokenCount

        droppedMessageCount = len(messagesCopy) - len(keptMessages)
        remainingPromptTokenBudget = promptTokenBudget - usedPromptTokenCount

        return PromptTokenBudgetTrimResult(
            keptMessages=keptMessages,
            wasTrimmed=droppedMessageCount > 0,
            usedPromptTokenCount=usedPromptTokenCount,
            droppedMessageCount=droppedMessageCount,
            promptTokenBudget=promptTokenBudget,
            remainingPromptTokenBudget=remainingPromptTokenBudget,
            tokenCountSource=self.tokenCounter.tokenCountSource,
        )

    def _getPromptTokenBudget(self) -> int | None:
        if self.config.contextSize is None or self.config.contextSize <= 0:
            return None

        promptTokenBudget = (
            self.config.contextSize - self.config.reservedResponseTokenCount - self.config.safetyMarginTokenCount
        )
        if promptTokenBudget <= 0:
            raise PromptTokenBudgetExceededError.forNoPromptTokensRemaining(
                contextSize=self.config.contextSize,
                reservedResponseTokenCount=self.config.reservedResponseTokenCount,
                safetyMarginTokenCount=self.config.safetyMarginTokenCount,
            )

        return promptTokenBudget


def makePromptTokenCounter(
    mode: PromptTokenBudgetMode,
    *,
    estimatedCharactersPerToken: float = 3.0,
) -> PromptTokenCounter:
    if mode == PromptTokenBudgetMode.NONE:
        return NoPromptTokenCounter()

    if mode == PromptTokenBudgetMode.ESTIMATED:
        return EstimatedPromptTokenCounter(
            estimatedCharactersPerToken=estimatedCharactersPerToken,
        )

    msg = f"unsupported prompt token budget mode: {mode}"  # TODO: Make this its own exception?
    raise ValueError(msg)
