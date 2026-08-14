# file: backend/tracing/tracer.py ; version: 5
from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, Self, cast

from backend.core.validation import requireInstance, requireString
from backend.tracing.builders import TraceEventBuilder, TraceSpanBuilder
from backend.tracing.context import (
    UNSET,
    TraceCorrelationContext,
    TraceCorrelationScope,
    TraceRuntimeContext,
    TraceSpanContext,
)
from backend.tracing.emergency import TraceEmergencyReporter
from backend.tracing.errors import (
    TraceClosedError,
    TraceContextError,
    TraceExplicitTypeOverrideError,
    TraceRecursivePublicationError,
    TraceSpanStateError,
)
from backend.tracing.publisher import TracePublisher
from backend.tracing.recordFactory import TraceRecordFactory
from backend.tracing.references import (
    TraceReferenceInput,
    normalizeTraceReferences,
)
from backend.tracing.spans import ActiveTraceSpan
from backend.tracing.typeDefinitions import (
    TRACE_EVENT,
    TRACE_SPAN,
    TraceEventType,
    TraceGeneratedType,
    TraceSpanType,
    TraceTypeDefinition,
)
from backend.tracing.typeRegistry import TraceTypeRegistry
from backend.tracing.validation import (
    TraceLevel,
    requireName,
    requireOutcomeName,
    requireTraceLevel,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Mapping

    from backend.core.ids import Uuid7Id
    from backend.tracing.destinations import TraceDestination
    from backend.tracing.exceptionSnapshot import ExceptionSnapshot
    from backend.tracing.ids import (
        TraceEventId,
        TraceSpanId,
        TraceTypeDefinitionId,
    )
    from backend.tracing.records import TraceRecord

__all__: list[str] = [
    "TRACE_SPAN_ABANDONED",
    "Tracer",
]


TRACE_SPAN_ABANDONED = TraceEventType(
    name="trace.span-abandoned",
    domain="",
    event=TraceGeneratedType(
        label="trace.span-abandoned",
        level="warning",
        displayName="Span abandoned",
    ),
)


class Tracer:
    """
    Coordinates trace types, runtime context, span lifecycles, and publication.

    One Tracer owns one TraceRecordFactory producer identity and therefore one
    producer-local evidence sequence. TraceSpanContext instances from another
    producer are rejected for structural containment.

    Active span lifecycle ownership remains local to the thread and asyncio
    task that started each span. Transferable TraceSpanContext values convey
    structure only.

    Ordinary tracing operations and tracer shutdown are serialized at the
    lifecycle boundary. Once shutdown begins, no new ordinary emission or span
    lifecycle transition may begin. Operations already admitted complete
    before shutdown takes ownership of remaining active spans.

    Destination publication is delegated to TracePublisher, while emergency
    reporting remains isolated from ordinary tracing.
    """

    def __init__(
        self,
        *,
        origin: str | None = None,
        destinations: Iterable[TraceDestination] = (),
        runtimeContext: TraceRuntimeContext | None = None,
        recordFactory: TraceRecordFactory | None = None,
        emergencyReporter: TraceEmergencyReporter | None = None,
    ) -> None:
        """
        Initializes one tracing lifecycle.

        Args:
            origin:
                Default origin used by parentless evidence when no explicit
                origin is supplied.
            destinations:
                Initial trace destinations.
            runtimeContext:
                Runtime context providing ambient spans and correlations.
                When omitted, a new independent context is created.
            recordFactory:
                Record factory owning the producer identity and sequence.
                When omitted, a new factory is created.
            emergencyReporter:
                Reporter for failures that cannot be safely published
                through ordinary tracing.

        Raises:
            TypeError:
                If supplied runtime components violate their runtime contracts.
            ValueError:
                If origin is not a valid trace name.

        """
        self._defaultOrigin = (
            None if origin is None else requireName(origin, "origin")
        )
        self._runtimeContext = (
            TraceRuntimeContext()
            if runtimeContext is None
            else requireInstance(
                runtimeContext,
                TraceRuntimeContext,
                "runtimeContext",
            )
        )
        self._recordFactory = (
            TraceRecordFactory()
            if recordFactory is None
            else requireInstance(
                recordFactory,
                TraceRecordFactory,
                "recordFactory",
            )
        )
        self._emergencyReporter = (
            TraceEmergencyReporter()
            if emergencyReporter is None
            else requireInstance(
                emergencyReporter,
                TraceEmergencyReporter,
                "emergencyReporter",
            )
        )
        self._traceTypeRegistry = TraceTypeRegistry()
        self._traceTypeLock = threading.RLock()
        self._activeSpanLock = threading.RLock()
        self._lifecycleLock = threading.RLock()
        self._activeSpans: dict[TraceSpanId, ActiveTraceSpan] = {}
        self._closing = False
        self._closed = False

        self._traceTypeRegistry.register(TRACE_EVENT.getDefinition())
        self._traceTypeRegistry.register(TRACE_SPAN.getDefinition())

        self._publisher = TracePublisher(
            getTraceTypeDefinitions=self.getTraceTypeDefinitions,
            destinations=destinations,
            emergencyReporter=self._emergencyReporter,
        )

    def __enter__(self) -> Self:
        """
        Enters this tracer as a synchronous context manager.

        Returns:
            This open Tracer.

        Raises:
            TraceClosedError:
                If the tracer is closing or has already been closed.

        """
        self._requireOpen()
        return self

    def __exit__(self, *args: object) -> bool:
        """
        Closes this tracer on synchronous context-manager exit.

        Returns:
            False so exceptions from the managed body are never suppressed.

        """
        self.close()
        return False

    async def __aenter__(self) -> Self:
        """
        Enters this tracer as an asynchronous context manager.

        Returns:
            This open Tracer.

        Raises:
            TraceClosedError:
                If the tracer is closing or has already been closed.

        """
        return self.__enter__()

    async def __aexit__(self, *args: object) -> bool:
        """
        Closes this tracer on asynchronous context-manager exit.

        Returns:
            False so exceptions from the managed body are never suppressed.

        """
        self.close()
        return False

    def event(
        self,
        traceType: TraceEventType | None = None,
    ) -> TraceEventBuilder:
        """
        Creates a fluent ordinary-event builder.

        Args:
            traceType:
                Optional explicitly declared event type. None selects the
                default TRACE_EVENT semantics.

        Returns:
            Builder bound to this tracer.

        Raises:
            TraceClosedError:
                If the tracer is closing or closed.
            TypeError:
                If traceType is not a TraceEventType.

        """
        self._requireOpen()

        if traceType is not None:
            requireInstance(traceType, TraceEventType, "traceType")

        return TraceEventBuilder(
            tracer=cast(Tracer, self),
            traceType=traceType,
        )

    def span(
        self,
        traceType: TraceSpanType | None = None,
    ) -> TraceSpanBuilder:
        """
        Creates a fluent span-start builder.

        Args:
            traceType:
                Optional explicitly declared span type. None selects the
                default TRACE_SPAN semantics.

        Returns:
            Builder bound to this tracer.

        Raises:
            TraceClosedError:
                If the tracer is closing or closed.
            TypeError:
                If traceType is not a TraceSpanType.

        """
        self._requireOpen()

        if traceType is not None:
            requireInstance(traceType, TraceSpanType, "traceType")

        return TraceSpanBuilder(
            tracer=cast(Tracer, self),
            traceType=traceType,
        )

    def emitEvent(
        self,
        *,
        traceType: TraceEventType | None = None,
        domain: str | None = None,
        level: TraceLevel | None = None,
        message: str = "",
        label: str | None = None,
        attributes: Mapping[object, object] | None = None,
        exception: BaseException | None = None,
        exceptionAttributes: Mapping[object, object] | None = None,
        includeExceptionStack: bool | None = None,
        span: TraceSpanContext | None = None,
        origin: str | None = None,
        causedBy: tuple[TraceReferenceInput, ...] = (),
    ) -> TraceEventId:
        """
        Emits one ordinary trace event directly.

        Explicit span and origin values are mutually exclusive. When neither
        is supplied, an ambient compatible span is used when available;
        otherwise the tracer's default origin is required.

        Args:
            traceType:
                Optional declared event type. None selects TRACE_EVENT.
            domain:
                Optional domain override. None uses the type definition's
                domain; an empty string explicitly selects the default domain.
            level:
                Optional presentation-level override.
            message:
                Human-readable event message.
            label:
                Optional record-local label for TRACE_EVENT. Explicitly
                declared event types do not permit label overrides.
            attributes:
                Optional immutable-value-compatible event attributes.
            exception:
                Optional exception snapshot to attach.
            exceptionAttributes:
                Optional catcher-owned attributes for exception capture.
            includeExceptionStack:
                Optional per-capture exception-stack override.
            span:
                Optional explicit structural parent span context.
            origin:
                Optional explicit origin for parentless evidence.
            causedBy:
                Causal or logical evidence references.

        Returns:
            Event identifier of the emitted record.

        Raises:
            TraceContextError:
                If structural parent/origin rules are violated.
            TraceClosedError:
                If the tracer is closing or closed.
            TraceRecursivePublicationError:
                If called recursively from destination delivery.
            TraceExplicitTypeOverrideError:
                If label attempts to override an explicitly declared type.

        """
        if span is not None and origin is not None:
            raise TraceContextError(
                "span and origin must not both be supplied.",
            )

        return self._emitEvent(
            traceType=traceType,
            domain=domain,
            level=level,
            message=message,
            label=label,
            attributes=attributes,
            exception=exception,
            exceptionAttributes=exceptionAttributes,
            includeExceptionStack=includeExceptionStack,
            span=span,
            origin=origin,
            causedBy=causedBy,
            useAmbient=span is None and origin is None,
        )

    def startSpan(
        self,
        *,
        traceType: TraceSpanType | None = None,
        domain: str | None = None,
        level: TraceLevel | None = None,
        message: str = "",
        label: str | None = None,
        attributes: Mapping[object, object] | None = None,
        exception: BaseException | None = None,
        exceptionAttributes: Mapping[object, object] | None = None,
        includeExceptionStack: bool | None = None,
        parent: TraceSpanContext | None = None,
        origin: str | None = None,
        causedBy: tuple[TraceReferenceInput, ...] = (),
    ) -> ActiveTraceSpan:
        """
        Starts one trace span directly.

        Explicit parent and origin values are mutually exclusive. When neither
        is supplied, an ambient compatible span is used as parent when
        available; otherwise the tracer's default origin is required.

        Args:
            traceType:
                Optional declared span type. None selects TRACE_SPAN.
            domain:
                Optional domain override. None uses the type definition's
                domain; an empty string explicitly selects the default domain.
            level:
                Optional presentation-level override for span-start evidence.
            message:
                Human-readable span-start message.
            label:
                Optional record-local start label for TRACE_SPAN. Explicitly
                declared span types do not permit label overrides.
            attributes:
                Optional immutable-value-compatible span-start attributes.
            exception:
                Optional exception snapshot to attach to span-start evidence.
            exceptionAttributes:
                Optional catcher-owned attributes for exception capture.
            includeExceptionStack:
                Optional per-capture exception-stack override.
            parent:
                Optional explicit structural parent span context.
            origin:
                Optional explicit origin for a root span.
            causedBy:
                Causal or logical evidence references.

        Returns:
            Lifecycle owner for the started span.

        Raises:
            TraceContextError:
                If structural parent/origin rules are violated.
            TraceClosedError:
                If the tracer is closing or closed.
            TraceRecursivePublicationError:
                If called recursively from destination delivery.
            TraceExplicitTypeOverrideError:
                If label attempts to override an explicitly declared type.

        """
        if parent is not None and origin is not None:
            raise TraceContextError(
                "parent and origin must not both be supplied.",
            )

        return self._startSpan(
            traceType=traceType,
            domain=domain,
            level=level,
            message=message,
            label=label,
            attributes=attributes,
            exception=exception,
            exceptionAttributes=exceptionAttributes,
            includeExceptionStack=includeExceptionStack,
            parent=parent,
            origin=origin,
            causedBy=causedBy,
            useAmbient=parent is None and origin is None,
        )

    def correlations(
        self,
        *,
        actantRunId: Uuid7Id | UNSET | None = UNSET,
        applicationId: Uuid7Id | UNSET | None = UNSET,
        applicationRunId: Uuid7Id | UNSET | None = UNSET,
    ) -> TraceCorrelationScope:
        """
        Creates a scope overriding Actant-owned correlation identities.

        UNSET inherits the ambient value, None explicitly clears it, and a
        Uuid7Id installs a replacement value.

        The returned scope operates directly on this tracer's configured
        TraceRuntimeContext and is independent of this Tracer's lifecycle. A
        shared runtime context may therefore continue carrying correlations
        after this tracer closes.

        Args:
            actantRunId:
                Actant-run override.
            applicationId:
                Application override.
            applicationRunId:
                Application-run override.

        Returns:
            Correlation scope bound to this tracer's runtime context.

        Raises:
            TypeError:
                If an override is neither UNSET, None, nor Uuid7Id.

        """
        return TraceCorrelationScope(
            runtimeContext=self._runtimeContext,
            actantRunId=actantRunId,
            applicationId=applicationId,
            applicationRunId=applicationRunId,
        )

    def addDestination(self, destination: TraceDestination) -> None:
        """
        Adds one trace destination.

        All currently registered trace-type definitions are offered to the
        destination before it becomes active for record delivery. Successful
        definition delivery is remembered by the publisher. Definitions whose
        initial delivery fails remain pending for that destination and may be
        retried by later publisher activity before dependent records are
        written.

        Args:
            destination:
                Structurally compatible trace destination.

        Raises:
            TraceClosedError:
                If the tracer is closing or closed.
            TraceDestinationContractError:
                If destination does not satisfy the runtime contract.
            TraceRecursivePublicationError:
                If called recursively from destination delivery.

        """
        with self._ordinaryOperation():
            self._publisher.addDestination(destination)

    def removeDestination(self, destination: TraceDestination) -> bool:
        """
        Removes one trace destination by identity.

        Args:
            destination:
                Destination object to remove.

        Returns:
            True if the exact active destination was removed.

        Raises:
            TraceClosedError:
                If the tracer is closing or closed.
            TraceDestinationContractError:
                If destination does not satisfy the runtime contract.
            TraceRecursivePublicationError:
                If called recursively from destination delivery.

        """
        with self._ordinaryOperation():
            return self._publisher.removeDestination(destination)

    def getPublisher(self) -> TracePublisher:
        """
        Returns the trace publisher.

        Returns:
            Publisher coordinating this tracer's destinations.

        """
        return self._publisher

    def getTraceTypeDefinitions(
        self,
    ) -> Mapping[TraceTypeDefinitionId, TraceTypeDefinition]:
        """
        Returns an immutable current-lifecycle definition snapshot.

        Returns:
            Mapping keyed by deterministic trace-type definition identity.

        """
        return self._traceTypeRegistry.getTraceTypeDefinitions()

    def getTraceTypeDefinition(
        self,
        traceTypeDefinitionId: TraceTypeDefinitionId,
    ) -> TraceTypeDefinition:
        """
        Returns one registered trace-type definition.

        Args:
            traceTypeDefinitionId:
                Deterministic identity to resolve.

        Returns:
            Registered definition.

        Raises:
            TraceTypeDefinitionNotFoundError:
                If no active definition has the supplied identity.

        """
        return self._traceTypeRegistry.getTraceTypeDefinition(
            traceTypeDefinitionId,
        )

    def getActiveSpanCount(self) -> int:
        """
        Returns the number of active lifecycle owners.

        Returns:
            Number of spans that have neither ended nor been abandoned.

        """
        with self._activeSpanLock:
            return len(self._activeSpans)

    def close(self) -> None:
        """
        Closes the tracer and abandons remaining active spans.

        Shutdown is serialized against ordinary tracing operations. Once this
        method enters the closing state, no new ordinary event emission, span
        start, span termination, or destination mutation may begin.

        Remaining active spans are first abandoned with context restoration
        where legally possible. Current-owner descendants are unwound in actual
        ambient LIFO order. If context-aware abandonment fails, the failure is
        emergency-reported and a final pass abandons any remaining lifecycle
        owners without ContextVar restoration.

        Every successful abandonment transition produces best-effort
        ``trace.span-abandoned`` evidence. Publication failure is reported
        through the emergency path and never rolls back lifecycle cleanup.

        Repeated close calls are idempotent.

        Raises:
            TraceRecursivePublicationError:
                If called from trace-destination publication.

        """
        if self._publisher.isPublishing():
            self._publisher.reportRecursiveTracingAttempt()
            raise TraceRecursivePublicationError(
                "Trace destinations cannot close their owning "
                "tracer while consuming trace evidence.",
            )

        with self._lifecycleLock:
            if self._closed:
                return

            if self._closing:
                return

            self._closing = True

            try:
                with self._activeSpanLock:
                    activeSpans = tuple(self._activeSpans.values())

                for activeSpan in reversed(activeSpans):
                    try:
                        self._abandonActiveSpanLocked(
                            activeSpan,
                            restoreIfOwned=True,
                        )
                    except Exception as err:  # noqa: BLE001
                        self._emergencyReporter.reportAbandonmentFailure(
                            spanId=activeSpan.spanId,
                            err=err,
                        )

                with self._activeSpanLock:
                    remainingSpans = tuple(self._activeSpans.values())

                for activeSpan in remainingSpans:
                    try:
                        self._abandonActiveSpanLocked(
                            activeSpan,
                            restoreIfOwned=False,
                        )
                    except Exception as err:  # noqa: BLE001
                        self._emergencyReporter.reportAbandonmentFailure(
                            spanId=activeSpan.spanId,
                            err=err,
                        )
            finally:
                self._closed = True
                self._closing = False

    def _emitEvent(
        self,
        *,
        traceType: TraceEventType | None,
        domain: str | None,
        level: TraceLevel | None,
        message: str,
        label: str | None,
        attributes: Mapping[object, object] | None,
        exception: BaseException | None,
        exceptionAttributes: Mapping[object, object] | None,
        includeExceptionStack: bool | None,
        span: TraceSpanContext | None,
        origin: str | None,
        causedBy: tuple[TraceReferenceInput, ...],
        useAmbient: bool,
        internalEmission: bool = False,
    ) -> TraceEventId:
        """
        Resolves, materializes, and publishes one trace event.

        Args:
            traceType:
                Event type to resolve.
            domain:
                Optional domain override.
            level:
                Optional presentation-level override.
            message:
                Human-readable event message.
            label:
                Optional record-local event label.
            attributes:
                Optional event attributes.
            exception:
                Optional exception to capture.
            exceptionAttributes:
                Optional catcher-owned exception attributes.
            includeExceptionStack:
                Optional exception-stack override.
            span:
                Optional explicit structural span context.
            origin:
                Optional explicit parentless origin.
            causedBy:
                Causal or logical evidence references.
            useAmbient:
                Whether ambient structural span context may be selected.
            internalEmission:
                Whether this is tracer-owned diagnostic emission that may run
                while the tracer is open or closing.

        Returns:
            Event identifier of the emitted record.

        """
        operation = (
            self._internalEmissionOperation()
            if internalEmission
            else self._ordinaryOperation()
        )

        with operation:
            resolvedType = (
                TRACE_EVENT
                if traceType is None
                else requireInstance(
                    traceType,
                    TraceEventType,
                    "traceType",
                )
            )
            definition = self._registerTraceType(resolvedType.getDefinition())
            generated = definition.event

            if generated is None:
                raise TraceContextError(
                    "Event trace type resolved without event data.",
                )

            isDefault = (
                definition.traceTypeDefinitionId
                == TRACE_EVENT.getDefinition().traceTypeDefinitionId
            )

            if label is not None:
                if not isDefault:
                    raise TraceExplicitTypeOverrideError(
                        "Declared TraceEventType labels cannot be overridden.",
                    )
                recordType = requireName(label, "label")
            else:
                recordType = generated.label

            resolvedDomain = self._resolveDomain(domain, definition.domain)
            resolvedLevel = (
                generated.level
                if level is None
                else requireTraceLevel(level)
            )
            resolvedMessage = requireString(message, "message")
            spanContext, resolvedOrigin = self._resolveParent(
                explicitSpan=span,
                explicitOrigin=origin,
                useAmbient=useAmbient,
            )
            correlations = self._resolveCorrelations(spanContext)
            exceptionSnapshot = self._captureException(
                exception,
                exceptionAttributes=exceptionAttributes,
                includeExceptionStack=includeExceptionStack,
            )
            references = normalizeTraceReferences(causedBy)
            record = self._recordFactory.createEvent(
                domain=resolvedDomain,
                recordType=recordType,
                traceTypeDefinitionId=definition.traceTypeDefinitionId,
                level=resolvedLevel,
                message=resolvedMessage,
                attributes=attributes,
                exceptionSnapshot=exceptionSnapshot,
                spanContext=spanContext,
                origin=resolvedOrigin,
                causedBy=references,
                correlations=correlations,
            )

            self._publishRecord(record)
            return record.eventId

    def _startSpan(
        self,
        *,
        traceType: TraceSpanType | None,
        domain: str | None,
        level: TraceLevel | None,
        message: str,
        label: str | None,
        attributes: Mapping[object, object] | None,
        exception: BaseException | None,
        exceptionAttributes: Mapping[object, object] | None,
        includeExceptionStack: bool | None,
        parent: TraceSpanContext | None,
        origin: str | None,
        causedBy: tuple[TraceReferenceInput, ...],
        useAmbient: bool,
    ) -> ActiveTraceSpan:
        """
        Resolves, emits, installs, and registers one active span.

        Returns:
            Lifecycle owner for the started span.

        """
        with self._ordinaryOperation():
            resolvedType = (
                TRACE_SPAN
                if traceType is None
                else requireInstance(traceType, TraceSpanType, "traceType"))
            definition = self._registerTraceType(
                resolvedType.getDefinition(),
            )
            started = definition.started

            if started is None:
                raise TraceContextError(
                    "Span trace type resolved without start data.",
                )

            isDefault = (
                definition.traceTypeDefinitionId
                == TRACE_SPAN.getDefinition().traceTypeDefinitionId
            )

            if label is not None:
                if not isDefault:
                    raise TraceExplicitTypeOverrideError(
                        "Declared TraceSpanType start "
                        "labels cannot be overridden.",
                    )
                startLabel = requireOutcomeName(label, "label")
            else:
                startLabel = started.label

            recordType = f"{definition.name}.{startLabel}"
            resolvedDomain = self._resolveDomain(domain, definition.domain)
            resolvedLevel = (
                started.level
                if level is None
                else requireTraceLevel(level)
            )
            resolvedMessage = requireString(message, "message")
            parentContext, resolvedOrigin = self._resolveParent(
                explicitSpan=parent,
                explicitOrigin=origin,
                useAmbient=useAmbient,
            )
            correlations = self._resolveCorrelations(parentContext)
            exceptionSnapshot = self._captureException(
                exception,
                exceptionAttributes=exceptionAttributes,
                includeExceptionStack=includeExceptionStack,
            )
            references = normalizeTraceReferences(causedBy)
            spanId = self._recordFactory.newSpanId()
            startRecord = self._recordFactory.createSpanStart(
                spanId=spanId,
                spanFamily=definition.name,
                parentContext=parentContext,
                domain=resolvedDomain,
                recordType=recordType,
                traceTypeDefinitionId=definition.traceTypeDefinitionId,
                level=resolvedLevel,
                message=resolvedMessage,
                attributes=attributes,
                exceptionSnapshot=exceptionSnapshot,
                origin=resolvedOrigin,
                causedBy=references,
                correlations=correlations,
            )

            self._publishRecord(startRecord)

            spanContext = TraceSpanContext(
                traceProducerId=self._recordFactory.getTraceProducerId(),
                spanId=spanId,
                spanStartEventId=startRecord.eventId,
                spanFamily=definition.name,
                correlations=correlations,
            )
            parentLease = self._runtimeContext.installSpan(spanContext)
            activeSpan = ActiveTraceSpan(
                tracer=cast(Tracer, self),
                traceType=resolvedType,
                definition=definition,
                spanId=spanId,
                startRecord=startRecord,
                spanContext=spanContext,
                domain=resolvedDomain,
                startedMonotonicNs=startRecord.timestampMonotonicNs,
                parentLease=parentLease,
            )

            with self._activeSpanLock:
                self._activeSpans[spanId] = activeSpan

            return activeSpan

    def _createSpanEndRecord(
        self,
        *,
        activeSpan: ActiveTraceSpan,
        recordType: str,
        level: TraceLevel,
        message: str,
        attributes: Mapping[object, object] | None,
        exceptionSnapshot: ExceptionSnapshot | None,
        outcome: str,
        causedBy: tuple[TraceReferenceInput, ...],
    ) -> TraceRecord:
        """
        Materializes terminal evidence for one owned active span.

        Returns:
            Immutable terminal span record.

        """
        spanContext = activeSpan.getContext()
        correlations = spanContext.correlations
        definition = self.getTraceTypeDefinition(
            activeSpan.definition.traceTypeDefinitionId,
        )

        return self._recordFactory.createSpanEnd(
            spanContext=spanContext,
            domain=activeSpan.domain,
            recordType=recordType,
            traceTypeDefinitionId=definition.traceTypeDefinitionId,
            level=requireTraceLevel(level),
            message=requireString(message, "message"),
            attributes=attributes,
            exceptionSnapshot=exceptionSnapshot,
            outcome=outcome,
            startedMonotonicNs=activeSpan.startedMonotonicNs,
            causedBy=normalizeTraceReferences(causedBy),
            correlations=correlations,
        )

    def _captureException(
        self,
        exception: BaseException | None,
        *,
        exceptionAttributes: Mapping[object, object] | None,
        includeExceptionStack: bool | None,
    ) -> ExceptionSnapshot | None:
        """
        Resolves optional exception capture arguments.

        Exception-specific attributes and stack overrides are invalid without
        an attached exception.

        Returns:
            Captured immutable exception snapshot, or None when no exception
            was supplied.

        Raises:
            TypeError:
                If exception is not a BaseException.
            ValueError:
                If exception-specific options are supplied without exception.

        """
        if exception is None:
            if exceptionAttributes is not None:
                raise ValueError(
                    "exceptionAttributes requires an attached exception.",
                )

            if includeExceptionStack is not None:
                raise ValueError(
                    "includeExceptionStack requires an attached exception.",
                )

            return None

        requireInstance(exception, BaseException, "exception")

        return self._recordFactory.captureException(
            exception,
            catcherAttributes=exceptionAttributes,
            includeStack=includeExceptionStack,
        )

    def _resolveParent(
        self,
        *,
        explicitSpan: TraceSpanContext | None,
        explicitOrigin: str | None,
        useAmbient: bool,
    ) -> tuple[TraceSpanContext | None, str | None]:
        """
        Resolves structural span containment and parentless origin.

        Explicit or ambient span contexts must belong to this tracer's record
        producer. Parentless evidence requires either an explicit origin or the
        tracer's configured default.

        Returns:
            A pair containing resolved structural span context and resolved
            parentless origin.

        Raises:
            TraceContextError:
                If structural containment or origin rules are violated.

        """
        if explicitSpan is not None:
            spanContext = requireInstance(
                explicitSpan,
                TraceSpanContext,
                "spanContext",
            )
            self._requireCompatibleSpanContext(spanContext)

            if explicitOrigin is not None:
                raise TraceContextError(
                    "A structurally contained record must not contain origin.",
                )

            return spanContext, None

        if explicitOrigin is not None:
            return None, requireName(explicitOrigin, "origin")

        if useAmbient:
            ambientSpan = self._runtimeContext.getCurrentSpan()

            if ambientSpan is not None:
                self._requireCompatibleSpanContext(ambientSpan)
                return ambientSpan, None

        if self._defaultOrigin is None:
            raise TraceContextError(
                "Parentless trace evidence requires explicit "
                "origin or a tracer default origin.",
            )

        return None, self._defaultOrigin

    def _resolveCorrelations(
        self,
        spanContext: TraceSpanContext | None,
    ) -> TraceCorrelationContext:
        """
        Resolves correlation identities for emitted evidence.

        Structural span correlations take precedence. Missing values are filled
        from the current ambient correlation context.

        Returns:
            Immutable resolved correlation context.

        """
        current = self._runtimeContext.getCurrentCorrelations()

        if spanContext is None:
            return current

        return spanContext.correlations.fillMissingFrom(current)

    def _resolveDomain(self, domain: str | None, default: str) -> str:
        """
        Resolves an optional domain override against a type default.

        Returns:
            Resolved domain string.

        """
        if domain is None:
            return default

        cleanDomain = requireString(domain, "domain")

        if cleanDomain == "":
            return ""

        return requireName(cleanDomain, "domain")

    def _registerTraceType(
        self,
        definition: TraceTypeDefinition,
    ) -> TraceTypeDefinition:
        """
        Registers one definition and publishes it when newly introduced.

        Returns:
            Canonical registered definition instance.

        """
        with self._traceTypeLock:
            registration = self._traceTypeRegistry.register(definition)

            if registration.isNew:
                self._publisher.publishTraceTypeDefinition(
                    registration.definition,
                )

            return registration.definition

    def _publishRecord(self, record: TraceRecord) -> None:
        """Publishes one already materialized trace record."""
        self._publisher.publish(record)

    def _finishActiveSpan(self, activeSpan: ActiveTraceSpan) -> None:
        """
        Removes one normally terminated lifecycle owner.

        Args:
            activeSpan:
                Exact lifecycle owner expected in active-span bookkeeping.

        Raises:
            TraceSpanStateError:
                If active-span bookkeeping no longer contains the exact owner.

        """
        with self._activeSpanLock:
            existing = self._activeSpans.get(activeSpan.spanId)

            if existing is not activeSpan:
                raise TraceSpanStateError(
                    "Active span bookkeeping does not contain this owner.",
                )

            del self._activeSpans[activeSpan.spanId]

    def _abandonActiveSpan(
        self,
        activeSpan: ActiveTraceSpan,
        *,
        restoreIfOwned: bool,
    ) -> bool:
        """
        Abandons one active span and reports every resulting transition.

        The tracer lifecycle lock serializes this transition against ordinary
        span completion and close(). If current-owner restoration is requested,
        the actual ambient stack is unwound in LIFO order through activeSpan.

        Args:
            activeSpan:
                Lifecycle owner to abandon.
            restoreIfOwned:
                Whether current-owner context leases should be unwound.

        Returns:
            True if activeSpan transitioned from active to abandoned during
            this call. False if it was no longer the registered active owner.

        Raises:
            TraceSpanStateError:
                If current-owner ambient stack state cannot be reconciled with
                active lifecycle bookkeeping.
            TraceContextError:
                If an owned ContextVar lease cannot be restored.

        """
        with self._lifecycleLock:
            return self._abandonActiveSpanLocked(
                activeSpan,
                restoreIfOwned=restoreIfOwned,
            )

    def _abandonActiveSpanLocked(
        self,
        activeSpan: ActiveTraceSpan,
        *,
        restoreIfOwned: bool,
    ) -> bool:
        """
        Performs one abandonment while the tracer lifecycle lock is held.

        Returns:
            True if activeSpan transitioned to abandoned during this call.

        """
        with self._activeSpanLock:
            existing = self._activeSpans.get(activeSpan.spanId)

        if existing is not activeSpan:
            return False

        if (
            restoreIfOwned
            and activeSpan._isCurrentOwner()
        ):
            self._abandonOwnedSpanStackThroughLocked(activeSpan)
            return True

        with self._activeSpanLock:
            existing = self._activeSpans.get(activeSpan.spanId)

            if existing is not activeSpan:
                return False

            activeSpan._abandon(restoreIfOwned=False)
            del self._activeSpans[activeSpan.spanId]

        self._publishSpanAbandonment(activeSpan)
        return True

    def _abandonOwnedSpanStackThroughLocked(
        self,
        targetSpan: ActiveTraceSpan,
    ) -> None:
        """
        Unwinds the current owned ambient span stack through targetSpan.

        Every encountered ambient span must correspond to the exact registered
        lifecycle owner and belong to the current thread and asyncio task.
        Lifecycle transition and active-owner removal occur atomically under
        the active-span lock. Abandonment evidence is emitted after that lock
        is released but while the tracer lifecycle remains serialized.

        Args:
            targetSpan:
                Active lifecycle owner through which the ambient stack is
                unwound.

        Raises:
            TraceSpanStateError:
                If the ambient stack cannot be reconciled with active lifecycle
                ownership or targetSpan cannot be reached.
            TraceContextError:
                If an owned context lease cannot be restored.

        """
        while True:
            currentContext = self._runtimeContext.getCurrentSpan()

            if currentContext is None:
                raise TraceSpanStateError(
                    "Owned active trace span is absent from its ambient "
                    "span stack.",
                )

            with self._activeSpanLock:
                currentSpan = self._activeSpans.get(currentContext.spanId)

                if currentSpan is None:
                    raise TraceSpanStateError(
                        "Ambient trace span has no active lifecycle owner.",
                    )

                if not currentSpan._isCurrentOwner():
                    raise TraceSpanStateError(
                        "Owned trace span recovery encountered an ambient span "
                        "owned by another execution context.",
                    )

                if currentSpan.getContext() != currentContext:
                    raise TraceSpanStateError(
                        "Ambient trace span context does not match its "
                        "registered lifecycle owner.",
                    )

                currentSpan._abandon(restoreIfOwned=True)
                del self._activeSpans[currentSpan.spanId]

            self._publishSpanAbandonment(currentSpan)

            if currentSpan is targetSpan:
                return

    def _publishSpanAbandonment(self, activeSpan: ActiveTraceSpan) -> None:
        """
        Publishes best-effort evidence for one completed abandonment
        transition.

        The lifecycle transition has already happened when this method is
        called. Publication failure is therefore reported through the emergency
        path and never attempts to roll back abandonment.

        Args:
            activeSpan:
                Span that has successfully transitioned to abandoned.

        """
        spanContext = activeSpan.getContext()

        try:
            self._emitEvent(
                traceType=TRACE_SPAN_ABANDONED,
                domain=None,
                level=None,
                message="Trace span was abandoned before terminal evidence.",
                label=None,
                attributes={
                    "spanFamily": spanContext.spanFamily,
                },
                exception=None,
                exceptionAttributes=None,
                includeExceptionStack=None,
                span=spanContext,
                origin=None,
                causedBy=(),
                useAmbient=False,
                internalEmission=True,
            )
        except Exception as err:  # noqa: BLE001
            self._emergencyReporter.reportAbandonmentFailure(
                spanId=spanContext.spanId,
                err=err,
            )

    def _reportManagedSpanFinalizationFailure(
        self,
        activeSpan: ActiveTraceSpan,
        err: Exception,
    ) -> None:
        """
        Reports a managed span finalization or recovery failure.

        Args:
            activeSpan:
                Span whose managed finalization or recovery failed.
            err:
                Failure to report through the emergency channel.

        """
        self._emergencyReporter.reportContextFailure(
            "Managed span finalization failed for "
            f"{activeSpan.spanId}: {type(err).__name__}: {err}",
        )

    def _getCurrentSpanContext(self) -> TraceSpanContext | None:
        """
        Returns the current ambient span context.

        Returns:
            Current structural span context, or None.

        """
        return self._runtimeContext.getCurrentSpan()

    def _requireCompatibleSpanContext(
        self,
        spanContext: TraceSpanContext,
    ) -> None:
        """
        Requires structural context to belong to this trace producer.

        Args:
            spanContext:
                Structural span context to validate.

        Raises:
            TraceContextError:
                If spanContext was created by another record producer.

        """
        if (
            spanContext.traceProducerId
            != self._recordFactory.getTraceProducerId()
        ):
            raise TraceContextError(
                "TraceSpanContext belongs to another trace producer.",
            )

    @contextlib.contextmanager
    def _ordinaryOperation(self) -> Generator[None]:
        """
        Serializes one ordinary tracing operation against tracer shutdown.

        The lifecycle lock remains held for the complete operation. close()
        therefore cannot begin after admission but before the operation commits
        its lifecycle effects.

        Yields:
            Control while ordinary tracing remains permitted.

        Raises:
            TraceClosedError:
                If the tracer is closing or closed.
            TraceRecursivePublicationError:
                If called while a destination is consuming trace evidence.

        """
        with self._lifecycleLock:
            self._requireEmissionAllowedLocked()
            yield

    @contextlib.contextmanager
    def _internalEmissionOperation(self) -> Generator[None]:
        """
        Admits tracer-owned diagnostic emission outside ordinary lifecycle
        rules.

        Internal emission may run while the tracer is open or closing. This
        supports abandonment evidence produced both by managed-span recovery
        and by close(). It is never permitted after the tracer is closed.

        Yields:
            Control while internal diagnostic emission is permitted.

        Raises:
            TraceClosedError:
                If the tracer is already closed.
            TraceRecursivePublicationError:
                If called while a destination is consuming trace evidence.

        """
        with self._lifecycleLock:
            if self._closed:
                raise TraceClosedError("Tracer is closed.")

            if self._publisher.isPublishing():
                self._publisher.reportRecursiveTracingAttempt()
                raise TraceRecursivePublicationError(
                    "Trace destinations cannot invoke tracing while "
                    "consuming trace evidence.",
                )

            yield

    def _requireEmissionAllowed(self) -> None:
        """
        Validates that an ordinary tracing operation may begin.

        This method performs validation only. Lifecycle-changing operations
        that must remain serialized against close() use _ordinaryOperation()
        for the complete operation.

        Raises:
            TraceClosedError:
                If the tracer is closing or closed.
            TraceRecursivePublicationError:
                If called while a destination is consuming trace evidence.

        """
        with self._lifecycleLock:
            self._requireEmissionAllowedLocked()

    def _requireEmissionAllowedLocked(self) -> None:
        """
        Requires ordinary emission while the lifecycle lock is held.

        Raises:
            TraceClosedError:
                If the tracer is closing or closed.
            TraceRecursivePublicationError:
                If called while a destination is consuming trace evidence.

        """
        if self._closing or self._closed:
            raise TraceClosedError("Tracer is closing or closed.")

        if self._publisher.isPublishing():
            self._publisher.reportRecursiveTracingAttempt()
            raise TraceRecursivePublicationError(
                "Trace destinations cannot invoke ordinary "
                "tracing while consuming trace evidence.",
            )

    def _requireOpen(self) -> None:
        """
        Requires the tracer lifecycle to remain open.

        Raises:
            TraceClosedError:
                If close() has begun or completed.

        """
        with self._lifecycleLock:
            if self._closing or self._closed:
                raise TraceClosedError("Tracer is closing or closed.")
