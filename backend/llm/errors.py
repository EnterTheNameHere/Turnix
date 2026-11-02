# file: backend/llm/errors.py ; version: 10
from __future__ import annotations

from backend.core.errors import ActantError

__all__: list[str] = [
    "LlmError",
    "LlmProviderConnectionError",
    "LlmProviderContractError",
    "LlmProviderError",
    "LlmProviderNotRegisteredError",
    "LlmProviderProtocolError",
    "LlmProviderRequestError",
    "LlmProviderUnavailableError",
    "LlmExecutionConflictError", "LlmContextLostError", "LlmStalePreviewError",
    "LlmUnsupportedExecutionError", "LlmExecutionCleanupError", "LlmExecutionOwnershipError",
]


class LlmError(ActantError):
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


class LlmExecutionConflictError(LlmProviderError):
    """Immediate ER-01 denial; safe conflict evidence never supplies retry policy."""

    def __init__(self, conflict):
        """Retains the neutral conflict without disclosing provider-private data."""
        super().__init__(f"LLM execution denied: {conflict.reason.value}.")
        self.conflict = conflict


class LlmContextLostError(LlmProviderError):
    """Required retained context is unavailable; rebuilding is not permitted."""


class LlmStalePreviewError(LlmProviderError):
    """A prepared query's issued execution binding no longer matches admission."""


class LlmUnsupportedExecutionError(LlmProviderError):
    """An explicit session or owned-context requirement cannot be fulfilled."""


class LlmExecutionOwnershipError(LlmProviderError):
    """A revoked, foreign or expired invocation/handle cannot authorize new use."""


class LlmExecutionCleanupError(LlmProviderError):
    """Explicit safe cleanup failure; never implicitly frees an unknown residency."""

    def __init__(self, effect=None, *, iteratorCloseFailed=False):
        """Retains bounded provider disposition without exposing callback exceptions."""
        super().__init__("LLM execution cleanup is failed, pending or uncertain.")
        self.effect = effect
        self.iteratorCloseFailed = iteratorCloseFailed
