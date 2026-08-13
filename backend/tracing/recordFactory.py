# file: backend/tracing/recordFactory.py ; version: 3
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer
from backend.core.validation import requireBool, requireInstance, requireNonNegativeInteger
from backend.tracing.context import TraceCorrelationContext, TraceSpanContext
from backend.tracing.errors import TraceInvariantError
from backend.tracing.exceptionSnapshot import (
    ExceptionSnapshot,
    captureExceptionSnapshot,
)
from backend.tracing.ids import (
    TraceEventId,
    TraceProducerId,
    TraceSpanId,
    TraceTypeDefinitionId,
)
from backend.tracing.records import TraceRecord

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from backend.tracing.references import TraceReference
    from backend.tracing.validation import TraceLevel

__all__: list[str] = [
    "TraceRecordFactory",
]


class TraceRecordFactory:
    """
    Creates normalized records for one trace producer.

    The factory owns one producer identity, producer-local sequence, and
    monotonic-clock domain. Sequence allocation is serialized across threads
    and committed only after a complete TraceRecord has been constructed
    successfully, so rejected record creation does not create gaps in the
    producer sequence.

    Record attributes are frozen before sequence allocation begins. The
    resulting TraceRecord independently freezes them again so the record
    remains responsible for guaranteeing its own immutable normalized state.
    """

    def __init__(
        self,
        *,
        traceProducerId: TraceProducerId | None = None,
        freezer: ImmutableValueFreezer | None = None,
        includeExceptionStacks: bool = True,
    ) -> None:
        """
        Initializes a trace-record factory.

        Args:
            traceProducerId:
                Producer identity that owns records created by this factory.
                When omitted, a new TraceProducerId is generated.
            freezer:
                Immutable-value freezer used to normalize record attributes.
                When omitted, a default ImmutableValueFreezer is created.
            includeExceptionStacks:
                Default controlling whether captured exception snapshots
                include formatted stack information.

        Raises:
            TypeError:
                If traceProducerId, freezer, or includeExceptionStacks has an
                unsupported runtime type.

        """
        self._traceProducerId = (
            TraceProducerId.new()
            if traceProducerId is None
            else requireInstance(
                traceProducerId,
                TraceProducerId,
                "traceProducerId",
            )
        )
        self._freezer = (
            ImmutableValueFreezer()
            if freezer is None
            else requireInstance(freezer, ImmutableValueFreezer, "freezer")
        )
        self._includeExceptionStacks = requireBool(
            includeExceptionStacks,
            "includeExceptionStacks",
        )
        self._sequenceLock = threading.Lock()
        self._nextSequence = 1

    def getTraceProducerId(self) -> TraceProducerId:
        """
        Returns the trace producer identifier.

        Returns:
            The identity owning this factory's sequence and monotonic-clock
            domain.

        """
        return self._traceProducerId

    def newSpanId(self) -> TraceSpanId:
        """
        Creates a new logical span identifier.

        Returns:
            A newly generated TraceSpanId.

        """
        return TraceSpanId.new()

    def freezeAttributes(
        self,
        attributes: Mapping[object, object] | None,
    ) -> Mapping[str, ImmutableValue]:
        """
        Freezes record attributes before record materialization.

        Args:
            attributes:
                Attribute mapping to normalize, or None for an empty mapping.

        Returns:
            An immutable deep-frozen attribute mapping.

        Raises:
            TypeError:
                If attributes or a contained value violates the immutable-value
                type contract.
            ValueError:
                If attributes violates configured immutable-value limits or
                contains another invalid immutable-value structure.

        """
        return self._freezer.freezeMapping(attributes, "attributes")

    def captureException(
        self,
        err: BaseException,
        *,
        catcherAttributes: Mapping[object, object] | None = None,
        includeStack: bool | None = None,
    ) -> ExceptionSnapshot:
        """
        Captures one serializable exception snapshot.

        Args:
            err:
                Exception whose diagnostic state is captured.
            catcherAttributes:
                Optional tracing metadata supplied by the exception catcher.
            includeStack:
                Per-capture stack inclusion override. None uses this factory's
                configured default.

        Returns:
            An immutable best-effort ExceptionSnapshot.

        Raises:
            TypeError:
                If err, includeStack, or catcherAttributes violates its
                runtime type contract.
            ValueError:
                If catcherAttributes violates immutable-value constraints.

        """
        resolvedIncludeStack = (
            self._includeExceptionStacks
            if includeStack is None
            else requireBool(includeStack, "includeStack")
        )

        return captureExceptionSnapshot(
            err,
            catcherAttributes=catcherAttributes,
            includeStack=resolvedIncludeStack,
        )

    def createEvent(
        self,
        *,
        domain: str,
        recordType: str,
        traceTypeDefinitionId: TraceTypeDefinitionId,
        level: TraceLevel,
        message: str,
        attributes: Mapping[object, object] | None,
        exceptionSnapshot: ExceptionSnapshot | None,
        spanContext: TraceSpanContext | None,
        origin: str | None,
        causedBy: tuple[TraceReference, ...],
        correlations: TraceCorrelationContext,
    ) -> TraceRecord:
        """
        Creates one ordinary event record.

        The event may be parentless or structurally contained by spanContext.
        Structural and value-level record invariants are enforced by
        TraceRecord before the producer sequence is committed.

        Args:
            domain:
                Trace domain associated with the event.
            recordType:
                Concrete emitted record type.
            traceTypeDefinitionId:
                Portable trace-type definition identifier.
            level:
                Presentation level for the event.
            message:
                Human-readable event message.
            attributes:
                Optional event attributes to freeze.
            exceptionSnapshot:
                Optional exception snapshot attached to the event.
            spanContext:
                Span structural context containing the event, or None for a
                parentless event.
            origin:
                Origin required by parentless evidence and absent from
                contained evidence.
            causedBy:
                Normalized causal or logical references.
            correlations:
                Correlation identities attached to the event.

        Returns:
            A normalized immutable event TraceRecord.

        Raises:
            TypeError:
                If spanContext, correlations, attributes, or another record
                field has an unsupported runtime type.
            ValueError:
                If attributes or resulting record fields violate their
                normalization or event structural contracts.

        """
        cleanSpanContext = _requireOptionalSpanContext(
            spanContext,
            "spanContext",
        )
        cleanCorrelations = requireInstance(
            correlations,
            TraceCorrelationContext,
            "correlations",
        )
        frozenAttributes = self.freezeAttributes(attributes)

        def buildRecord(
            eventId: TraceEventId,
            sequence: int,
            unixNs: int,
            monotonicNs: int,
        ) -> TraceRecord:
            return TraceRecord(
                eventId=eventId,
                traceProducerId=self._traceProducerId,
                sequence=sequence,
                timestampUnixNs=unixNs,
                timestampMonotonicNs=monotonicNs,
                kind="event",
                domain=domain,
                type=recordType,
                traceTypeDefinitionId=traceTypeDefinitionId,
                level=level,
                message=message,
                attributes=frozenAttributes,
                exceptionSnapshot=exceptionSnapshot,
                spanId=(
                    None
                    if cleanSpanContext is None
                    else cleanSpanContext.spanId
                ),
                spanStartEventId=(
                    None
                    if cleanSpanContext is None
                    else cleanSpanContext.spanStartEventId
                ),
                origin=origin,
                causedBy=causedBy,
                actantRunId=cleanCorrelations.actantRunId,
                applicationId=cleanCorrelations.applicationId,
                applicationRunId=cleanCorrelations.applicationRunId,
            )

        return self._materializeRecord(buildRecord)

    def createSpanStart(
        self,
        *,
        spanId: TraceSpanId,
        spanFamily: str,
        parentContext: TraceSpanContext | None,
        domain: str,
        recordType: str,
        traceTypeDefinitionId: TraceTypeDefinitionId,
        level: TraceLevel,
        message: str,
        attributes: Mapping[object, object] | None,
        exceptionSnapshot: ExceptionSnapshot | None,
        origin: str | None,
        causedBy: tuple[TraceReference, ...],
        correlations: TraceCorrelationContext,
    ) -> TraceRecord:
        """
        Creates the start record for one logical span.

        The record establishes span identity, span family, and optional parent
        ancestry. Structural and value-level invariants are enforced before
        the producer sequence is committed.

        Args:
            spanId:
                Identifier of the logical span being started.
            spanFamily:
                Trace-type family of the logical span.
            parentContext:
                Parent span context, or None for a root span.
            domain:
                Trace domain associated with the span.
            recordType:
                Concrete emitted span-start record type.
            traceTypeDefinitionId:
                Portable span-type definition identifier.
            level:
                Presentation level for the start record.
            message:
                Human-readable start message.
            attributes:
                Optional span-start attributes to freeze.
            exceptionSnapshot:
                Optional exception snapshot attached to the start record.
            origin:
                Origin required for root spans and absent from child spans.
            causedBy:
                Normalized causal or logical references.
            correlations:
                Correlation identities attached to the span start.

        Returns:
            A normalized immutable span-start TraceRecord.

        Raises:
            TypeError:
                If parentContext, correlations, attributes, or another record
                field has an unsupported runtime type.
            ValueError:
                If attributes or resulting record fields violate their
                normalization or span-start structural contracts.

        """
        cleanParentContext = _requireOptionalSpanContext(
            parentContext,
            "parentContext",
        )
        cleanCorrelations = requireInstance(
            correlations,
            TraceCorrelationContext,
            "correlations",
        )
        frozenAttributes = self.freezeAttributes(attributes)

        def buildRecord(
            eventId: TraceEventId,
            sequence: int,
            unixNs: int,
            monotonicNs: int,
        ) -> TraceRecord:
            return TraceRecord(
                eventId=eventId,
                traceProducerId=self._traceProducerId,
                sequence=sequence,
                timestampUnixNs=unixNs,
                timestampMonotonicNs=monotonicNs,
                kind="spanStart",
                domain=domain,
                type=recordType,
                traceTypeDefinitionId=traceTypeDefinitionId,
                level=level,
                message=message,
                attributes=frozenAttributes,
                exceptionSnapshot=exceptionSnapshot,
                spanId=spanId,
                spanStartEventId=eventId,
                parentSpanId=(
                    None
                    if cleanParentContext is None
                    else cleanParentContext.spanId
                ),
                spanFamily=spanFamily,
                origin=origin,
                causedBy=causedBy,
                actantRunId=cleanCorrelations.actantRunId,
                applicationId=cleanCorrelations.applicationId,
                applicationRunId=cleanCorrelations.applicationRunId,
            )

        return self._materializeRecord(buildRecord)

    def createSpanEnd(
        self,
        *,
        spanContext: TraceSpanContext,
        domain: str,
        recordType: str,
        traceTypeDefinitionId: TraceTypeDefinitionId,
        level: TraceLevel,
        message: str,
        attributes: Mapping[object, object] | None,
        exceptionSnapshot: ExceptionSnapshot | None,
        outcome: str,
        startedMonotonicNs: int,
        causedBy: tuple[TraceReference, ...],
        correlations: TraceCorrelationContext,
    ) -> TraceRecord:
        """
        Creates one terminal record for an active span.

        Duration is derived from this factory's current monotonic clock and the
        supplied start timestamp. A current monotonic value preceding the
        supplied start timestamp is treated as an invariant failure rather than
        being clamped to zero, because doing so would falsify elapsed-time
        evidence.

        Args:
            spanContext:
                Structural context of the span being terminated.
            domain:
                Trace domain associated with the span.
            recordType:
                Concrete emitted terminal record type.
            traceTypeDefinitionId:
                Portable span-type definition identifier.
            level:
                Presentation level for the terminal record.
            message:
                Human-readable terminal message.
            attributes:
                Optional terminal attributes to freeze.
            exceptionSnapshot:
                Optional exception snapshot attached to the terminal record.
            outcome:
                Terminal span outcome.
            startedMonotonicNs:
                Monotonic timestamp recorded when the span began.
            causedBy:
                Normalized causal or logical references.
            correlations:
                Correlation identities attached to the terminal record.

        Returns:
            A normalized immutable span-end TraceRecord.

        Raises:
            TypeError:
                If spanContext, correlations, startedMonotonicNs, attributes,
                or another record field has an unsupported runtime type.
            ValueError:
                If startedMonotonicNs is negative, attributes are invalid, or
                the resulting record violates its span-end contract.
            TraceInvariantError:
                If the current monotonic timestamp precedes the supplied span
                start timestamp.

        """
        cleanSpanContext = requireInstance(
            spanContext,
            TraceSpanContext,
            "spanContext",
        )
        cleanCorrelations = requireInstance(
            correlations,
            TraceCorrelationContext,
            "correlations",
        )
        cleanStartedMonotonicNs = requireNonNegativeInteger(
            startedMonotonicNs,
            "startedMonotonicNs",
        )
        frozenAttributes = self.freezeAttributes(attributes)

        def buildRecord(
            eventId: TraceEventId,
            sequence: int,
            unixNs: int,
            monotonicNs: int,
        ) -> TraceRecord:
            if monotonicNs < cleanStartedMonotonicNs:
                raise TraceInvariantError(
                    "Span end monotonic timestamp precedes its start "
                    "timestamp within the trace producer clock domain; "
                    f"producer={self._traceProducerId}, "
                    f"startedMonotonicNs={cleanStartedMonotonicNs}, "
                    f"endedMonotonicNs={monotonicNs}",
                )

            durationNs = monotonicNs - cleanStartedMonotonicNs

            return TraceRecord(
                eventId=eventId,
                traceProducerId=self._traceProducerId,
                sequence=sequence,
                timestampUnixNs=unixNs,
                timestampMonotonicNs=monotonicNs,
                kind="spanEnd",
                domain=domain,
                type=recordType,
                traceTypeDefinitionId=traceTypeDefinitionId,
                level=level,
                message=message,
                attributes=frozenAttributes,
                exceptionSnapshot=exceptionSnapshot,
                spanId=cleanSpanContext.spanId,
                spanStartEventId=cleanSpanContext.spanStartEventId,
                spanFamily=cleanSpanContext.spanFamily,
                outcome=outcome,
                durationNs=durationNs,
                causedBy=causedBy,
                actantRunId=cleanCorrelations.actantRunId,
                applicationId=cleanCorrelations.applicationId,
                applicationRunId=cleanCorrelations.applicationRunId,
            )

        return self._materializeRecord(buildRecord)

    def _materializeRecord(
        self,
        buildRecord: Callable[
            [TraceEventId, int, int, int],
            TraceRecord,
        ],
    ) -> TraceRecord:
        """
        Materializes one record and commits its producer sequence on success.

        The producer sequence lock remains held while the envelope is created
        and the TraceRecord is validated. If record construction raises, the
        candidate sequence remains available for the next successful record.
        """
        with self._sequenceLock:
            eventId = TraceEventId.new()
            sequence = self._nextSequence
            unixNs = time.time_ns()
            monotonicNs = time.monotonic_ns()

            record = buildRecord(
                eventId,
                sequence,
                unixNs,
                monotonicNs,
            )

            self._nextSequence += 1
            return record


def _requireOptionalSpanContext(
    value: TraceSpanContext | None,
    name: str,
) -> TraceSpanContext | None:
    """
    Validates an optional structural span context.

    Args:
        value:
            Span context to validate, or None when no span relationship is
            present.
        name:
            Diagnostic name identifying the value.

    Returns:
        The validated value unchanged.

    Raises:
        TypeError:
            If value is neither None nor a TraceSpanContext.

    """
    if value is None:
        return None

    return requireInstance(value, TraceSpanContext, name)
