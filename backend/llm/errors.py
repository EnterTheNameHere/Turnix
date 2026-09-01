# file: backend/llm/errors.py ; version: 8
from __future__ import annotations

__all__: list[str] = [
    "LlmError",
    "LlmProviderConnectionError",
    "LlmProviderContractError",
    "LlmProviderError",
    "LlmProviderNotRegisteredError",
    "LlmProviderProtocolError",
    "LlmProviderRequestError",
    "LlmProviderUnavailableError",
]


class LlmError(RuntimeError):
    """Base class for current Actant LLM-domain runtime failures."""


class LlmProviderError(LlmError):
    """Base class for LLM provider failures."""


class LlmProviderNotRegisteredError(LlmProviderError):
    """Raised when a requested LLM provider is not registered."""


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
