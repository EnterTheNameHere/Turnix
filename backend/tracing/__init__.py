# file: backend/tracing/__init__.py ; version: 3
"""
Provides Actant tracing infrastructure.

The package defines deterministic trace-type identifiers, immutable trace
records, event and span emission, runtime context propagation, correlation
contexts, exception snapshots, destination delivery, emergency reporting,
and terminal presentation.

Trace-type definitions are content-addressed through canonical JSON and
SHA-256 identities so emitted evidence can refer to stable portable
definitions. Tracers maintain producer-local sequence ordering, explicit
span relationships, and immutable record content while isolating ordinary
tracing from destination failures.

Tracer-owned record factories, type registries, publishers, and their mutable
registration state are internal implementation machinery and are not exposed
through the package API.
"""
from __future__ import annotations

from backend.tracing.builders import TraceEventBuilder, TraceSpanBuilder
from backend.tracing.canonical import canonicalJson
from backend.tracing.context import (
    TraceCorrelationContext,
    TraceCorrelationScope,
    TraceRuntimeContext,
    TraceSpanContext,
)
from backend.tracing.destinations import (
    TraceDestination,
    TraceSinkDestination,
)
from backend.tracing.emergency import TraceEmergencyReporter
from backend.tracing.errors import (
    TraceBuilderConsumedError,
    TraceClosedError,
    TraceContextError,
    TraceDestinationContractError,
    TraceDestinationStateError,
    TraceError,
    TraceExplicitTypeOverrideError,
    TraceInvariantError,
    TraceRecursivePublicationError,
    TraceSpanOwnershipError,
    TraceSpanStateError,
    TraceTypeConflictError,
    TraceTypeDefinitionCollisionError,
    TraceTypeDefinitionNotFoundError,
    TraceTypeRegistrationError,
    TraceUnknownOutcomeError,
    TraceUseError,
)
from backend.tracing.exceptionSnapshot import (
    ExceptionSnapshot,
    captureExceptionSnapshot,
)
from backend.tracing.ids import (
    TraceDestinationRegistrationId,
    TraceEventId,
    TraceProducerId,
    TraceSpanId,
    TraceTypeDefinitionId,
)
from backend.tracing.records import TraceRecord
from backend.tracing.references import TraceReference, TraceReferenceInput, normalizeTraceReferences
from backend.tracing.spans import ActiveTraceSpan
from backend.tracing.terminalDestination import (
    TerminalColorMode,
    TerminalTraceDestination,
)
from backend.tracing.tracer import (
    TRACE_DESTINATION_ADDED,
    TRACE_DESTINATION_FAILED,
    TRACE_DESTINATION_RECOVERED,
    TRACE_DESTINATION_REMOVED,
    TRACE_PRODUCER_READY,
    TRACE_PRODUCER_STOPPED,
    TRACE_PRODUCER_STOPPING,
    TRACE_SPAN_ABANDONED,
    TraceProducerStartContext,
    Tracer,
)
from backend.tracing.typeDefinitions import (
    DEFAULT_CANCELLED,
    DEFAULT_COMPLETED,
    DEFAULT_ERRORED,
    DEFAULT_EVENT,
    DEFAULT_FAILED,
    DEFAULT_STARTED,
    STANDARD_TRACE_SPAN_OUTCOMES,
    TRACE_EVENT,
    TRACE_SPAN,
    TRACE_TYPE_DEFINITION_SCHEMA_VERSION,
    TraceEventType,
    TraceGeneratedType,
    TraceSpanType,
    TraceTypeDefinition,
    TraceTypeDefinitionKind,
)
from backend.tracing.validation import (
    TRACE_LEVELS,
    TRACE_RECORD_KINDS,
    TraceLevel,
    TraceRecordKind,
)

__all__: list[str] = [
    "DEFAULT_CANCELLED",
    "DEFAULT_COMPLETED",
    "DEFAULT_ERRORED",
    "DEFAULT_EVENT",
    "DEFAULT_FAILED",
    "DEFAULT_STARTED",
    "STANDARD_TRACE_SPAN_OUTCOMES",
    "TRACE_DESTINATION_ADDED",
    "TRACE_DESTINATION_FAILED",
    "TRACE_DESTINATION_RECOVERED",
    "TRACE_DESTINATION_REMOVED",
    "TRACE_EVENT",
    "TRACE_LEVELS",
    "TRACE_PRODUCER_READY",
    "TRACE_PRODUCER_STOPPED",
    "TRACE_PRODUCER_STOPPING",
    "TRACE_RECORD_KINDS",
    "TRACE_SPAN",
    "TRACE_SPAN_ABANDONED",
    "TRACE_TYPE_DEFINITION_SCHEMA_VERSION",
    "ActiveTraceSpan",
    "ExceptionSnapshot",
    "TerminalColorMode",
    "TerminalTraceDestination",
    "TraceBuilderConsumedError",
    "TraceClosedError",
    "TraceContextError",
    "TraceCorrelationContext",
    "TraceCorrelationScope",
    "TraceDestination",
    "TraceDestinationContractError",
    "TraceDestinationRegistrationId",
    "TraceDestinationStateError",
    "TraceEmergencyReporter",
    "TraceError",
    "TraceEventBuilder",
    "TraceEventId",
    "TraceEventType",
    "TraceExplicitTypeOverrideError",
    "TraceGeneratedType",
    "TraceInvariantError",
    "TraceLevel",
    "TraceProducerId",
    "TraceProducerStartContext",
    "TraceRecord",
    "TraceRecordKind",
    "TraceRecursivePublicationError",
    "TraceReference",
    "TraceReferenceInput",
    "TraceRuntimeContext",
    "TraceSinkDestination",
    "TraceSpanBuilder",
    "TraceSpanContext",
    "TraceSpanId",
    "TraceSpanOwnershipError",
    "TraceSpanStateError",
    "TraceSpanType",
    "TraceTypeConflictError",
    "TraceTypeDefinition",
    "TraceTypeDefinitionCollisionError",
    "TraceTypeDefinitionId",
    "TraceTypeDefinitionKind",
    "TraceTypeDefinitionNotFoundError",
    "TraceTypeRegistrationError",
    "TraceUnknownOutcomeError",
    "TraceUseError",
    "Tracer",
    "canonicalJson",
    "captureExceptionSnapshot",
    "normalizeTraceReferences",
]
