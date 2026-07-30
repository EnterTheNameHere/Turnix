# file: backend/llm/errors.py ; version 5
from __future__ import annotations

from backend.core.errors import ActantError

__all__: list[str] = [
    "LlmError",
    "LlmInputRejectedError",
    "LlmPipelineStateError",
    "LlmPromptBudgetError",
    "LlmProviderAlreadyRegisteredError",
    "LlmProviderConnectionError",
    "LlmProviderContractError",
    "LlmProviderError",
    "LlmProviderNotRegisteredError",
    "LlmProviderProtocolError",
    "LlmProviderUnavailableError",
]


class LlmError(ActantError):
    """Base class for LLM-domain exceptions."""


class LlmInputRejectedError(LlmError):
    """Raised when a hook rejects the pipeline input."""

    def __init__(
        self,
        message: str,
        *,
        purposeId: str,
        ownerId: str,
        reason: str,
    ) -> None:
        super().__init__(message)
        self.purposeId = purposeId
        self.ownerId = ownerId
        self.reason = reason


class LlmPipelineStateError(LlmError):
    """Raised when a pipeline stage leaves the run in an invalid state."""


class LlmPromptBudgetError(LlmError):
    """Raised when a prompt cannot fit within the effective budget."""


class LlmProviderError(LlmError):
    """Base class for LLM provider exceptions."""


class LlmProviderNotRegisteredError(LlmProviderError):
    """Raised when a requested LLM provider is not registered."""


class LlmProviderAlreadyRegisteredError(LlmProviderError):
    """Raised when provider registration conflicts with an existing identity."""


class LlmProviderContractError(LlmProviderError):
    """Raised when a provider object violates the provider interface contract."""


class LlmProviderUnavailableError(LlmProviderError):
    """Raised when a registered LLM provider is currently unavailable."""


class LlmProviderConnectionError(LlmProviderError):
    """Raised when Actant cannot connect to an LLM provider."""


class LlmProviderProtocolError(LlmProviderError):
    """Raised when an LLM provider violates its protocol contract."""
