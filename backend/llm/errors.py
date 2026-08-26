# file: backend/llm/errors.py ; version: 7
from __future__ import annotations

from backend.core.errors import ActantError
from backend.core.validation import (
    requireExactNonBlankString,
    requireInstance,
    requireNonBlankString,
)
from backend.pack.packCodeEntry import PackCodeEntryInstanceId

__all__: list[str] = [
    "LlmError",
    "LlmInputRejectedError",
    "LlmPipelineStateError",
    "LlmProviderAlreadyRegisteredError",
    "LlmProviderConnectionError",
    "LlmProviderContractError",
    "LlmProviderError",
    "LlmProviderNotRegisteredError",
    "LlmProviderProtocolError",
    "LlmProviderRequestError",
    "LlmProviderUnavailableError",
    "LlmQueryBudgetError",
]


class LlmError(ActantError):
    """Base class for LLM-domain exceptions."""


class LlmInputRejectedError(LlmError):
    """Raised when a hook rejects the current pipeline input."""

    def __init__(
        self,
        message: str,
        *,
        purposeId: str,
        ownerId: PackCodeEntryInstanceId,
        reason: str,
    ) -> None:
        """Initializes an LLM input-rejection error."""
        requireNonBlankString(message, "message")
        requireExactNonBlankString(purposeId, "purposeId")
        requireInstance(ownerId, PackCodeEntryInstanceId, "ownerId")
        requireNonBlankString(reason, "reason")

        super().__init__(message)
        self.purposeId = purposeId
        self.ownerId = ownerId
        self.reason = reason


class LlmPipelineStateError(LlmError):
    """Raised when a pipeline stage leaves the run in an invalid state."""


class LlmQueryBudgetError(LlmError):
    """Raised when LLM inference input cannot satisfy the effective query budget."""


class LlmProviderError(LlmError):
    """Base class for LLM provider exceptions."""


class LlmProviderNotRegisteredError(LlmProviderError):
    """Raised when a requested LLM provider is not registered."""


class LlmProviderAlreadyRegisteredError(LlmProviderError):
    """Raised when provider registration conflicts with an existing identity."""


class LlmProviderContractError(LlmProviderError):
    """Raised when a provider object violates the provider interface contract."""


class LlmProviderRequestError(LlmProviderError):
    """Raised when a provider cannot execute a supplied call request."""


class LlmProviderUnavailableError(LlmProviderError):
    """Raised when a registered LLM provider is currently unavailable."""


class LlmProviderConnectionError(LlmProviderError):
    """Raised when Actant cannot connect to an LLM provider."""


class LlmProviderProtocolError(LlmProviderError):
    """Raised when an LLM provider violates its streaming protocol contract."""
