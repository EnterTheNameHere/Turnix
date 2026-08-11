# file: backend/tracing/errors.py ; version: 3
from __future__ import annotations

from backend.core.errors import ActantError, CoreInvariantError

__all__: list[str] = [
    "TraceBuilderConsumedError",
    "TraceClosedError",
    "TraceContextError",
    "TraceDestinationContractError",
    "TraceError",
    "TraceExplicitTypeOverrideError",
    "TraceInvariantError",
    "TraceRecursivePublicationError",
    "TraceSpanOwnershipError",
    "TraceSpanStateError",
    "TraceTypeConflictError",
    "TraceTypeDefinitionCollisionError",
    "TraceTypeDefinitionNotFoundError",
    "TraceTypeRegistrationError",
    "TraceUnknownOutcomeError",
    "TraceUseError",
]


class TraceError(ActantError):
    """Base class for tracing-domain exceptions."""


class TraceUseError(TraceError):
    """Raised when a caller uses the tracing API inconsistently."""


class TraceClosedError(TraceUseError):
    """Raised when emission is attempted through a closed tracer."""


class TraceBuilderConsumedError(TraceUseError):
    """Raised when a consumed trace builder is used again."""


class TraceSpanStateError(TraceUseError):
    """Raised when a span lifecycle operation is invalid for its state."""


class TraceSpanOwnershipError(TraceUseError):
    """Raised when another execution context attempts to end a span."""


class TraceContextError(TraceUseError):
    """Raised when trace context installation or restoration is invalid."""


class TraceRecursivePublicationError(TraceUseError):
    """Raised when a destination attempts to emit ordinary tracing."""


class TraceExplicitTypeOverrideError(TraceUseError):
    """Raised when record-local type text overrides a declared trace type."""


class TraceUnknownOutcomeError(TraceUseError):
    """Raised when a declared span type does not define an end outcome."""


class TraceTypeRegistrationError(TraceError):
    """Base class for active trace-type registration failures."""


class TraceTypeConflictError(TraceTypeRegistrationError):
    """Raised when one active trace-type name resolves to different IDs."""


class TraceTypeDefinitionCollisionError(TraceTypeRegistrationError):
    """Raised when one deterministic ID resolves to different definitions."""


class TraceTypeDefinitionNotFoundError(TraceError, KeyError):
    """Raised when the active registry does not contain a requested ID."""


class TraceDestinationContractError(TraceUseError):
    """Raised when an object does not implement the destination contract."""


class TraceInvariantError(TraceError, CoreInvariantError):
    """Raised when trusted tracing code detects an impossible state."""
