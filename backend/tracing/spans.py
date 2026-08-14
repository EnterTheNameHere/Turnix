# file: backend/tracing/spans.py ; version: 4
from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import TYPE_CHECKING, Self, cast

from backend.core.validation import requireInstance, requireString
from backend.tracing.errors import (
    TraceExplicitTypeOverrideError,
    TraceSpanOwnershipError,
    TraceSpanStateError,
    TraceUnknownOutcomeError,
)
from backend.tracing.typeDefinitions import (
    TRACE_SPAN,
    TraceGeneratedType,
    TraceSpanType,
    TraceTypeDefinition,
)
from backend.tracing.validation import (
    TraceLevel,
    requireOutcomeName,
    requireTraceLevel,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    from backend.tracing.context import TraceSpanContext, _ContextLease
    from backend.tracing.ids import TraceEventId, TraceSpanId
    from backend.tracing.records import TraceRecord
    from backend.tracing.references import TraceReferenceInput
    from backend.tracing.tracer import Tracer

__all__: list[str] = [
    "ActiveTraceSpan",
]


class ActiveTraceSpan:
    """
    Owns the terminal lifecycle operation for one started trace span.

    An active span is bound to the thread and asyncio task that started it.
    Its TraceSpanContext may be transferred for structural trace relationships,
    but terminal lifecycle ownership itself is not transferable.

    Normal terminal operations require this span to be the current ambient
    span in its owner execution context. Recovery and tracer shutdown may
    abandon the span instead of producing terminal span evidence.
    """

    def __init__(
        self,
        *,
        tracer: Tracer,
        traceType: TraceSpanType,
        definition: TraceTypeDefinition,
        spanId: TraceSpanId,
        startRecord: TraceRecord,
        spanContext: TraceSpanContext,
        domain: str,
        startedMonotonicNs: int,
        parentLease: _ContextLease[TraceSpanContext | None],
    ) -> None:
        """
        Initializes lifecycle ownership for an already emitted span start.

        Args:
            tracer:
                Tracer that owns the span lifecycle.
            traceType:
                Span type used to resolve terminal outcomes.
            definition:
                Registered portable definition for traceType.
            spanId:
                Logical span identifier.
            startRecord:
                Successfully materialized and published span-start record.
            spanContext:
                Transferable structural context representing this span.
            domain:
                Resolved trace domain for terminal evidence.
            startedMonotonicNs:
                Monotonic timestamp captured for the span-start record.
            parentLease:
                Context lease installed when this span became ambient.

        """
        self._tracer = tracer
        self._traceType = traceType
        self._definition = definition
        self._spanId = spanId
        self._startRecord = startRecord
        self._spanContext = spanContext
        self._domain = domain
        self._startedMonotonicNs = startedMonotonicNs
        self._parentLease = parentLease
        self._ownerThreadId = threading.get_ident()
        self._ownerTask = _currentAsyncioTask()
        self._ended = False
        self._managed = False
        self._abandoned = False

    @property
    def spanId(self) -> TraceSpanId:
        """
        Returns the logical span identifier.

        Returns:
            Identifier shared by all structural evidence for this span.

        """
        return self._spanId

    @property
    def spanStartEventId(self) -> TraceEventId:
        """
        Returns the event identifier of the span-start record.

        Returns:
            Event identifier that established this logical span.

        """
        return self._startRecord.eventId

    @property
    def domain(self) -> str:
        """
        Returns the resolved trace domain.

        Returns:
            Domain inherited by terminal evidence for this span.

        """
        return self._domain

    @property
    def definition(self) -> TraceTypeDefinition:
        """
        Returns the registered portable span-type definition.

        Returns:
            Definition used to interpret this span's generated record types.

        """
        return self._definition

    @property
    def startedMonotonicNs(self) -> int:
        """
        Returns the span-start monotonic timestamp.

        Returns:
            Nonnegative monotonic timestamp used to derive terminal duration.

        """
        return self._startedMonotonicNs

    def getContext(self) -> TraceSpanContext:
        """
        Returns transferable structural context without lifecycle ownership.

        The returned context may be supplied to tracing operations that need
        to establish structural containment. It does not grant permission to
        terminate this ActiveTraceSpan.

        Returns:
            Immutable structural context representing this span.

        """
        return self._spanContext

    def complete(
        self,
        *,
        message: str = "",
        level: TraceLevel | None = None,
        attributes: Mapping[object, object] | None = None,
        exception: BaseException | None = None,
        exceptionAttributes: Mapping[object, object] | None = None,
        includeExceptionStack: bool | None = None,
        label: str | None = None,
        causedBy: tuple[TraceReferenceInput, ...] = (),
    ) -> TraceEventId:
        """
        Ends the span with the standard completed outcome.

        Args:
            message:
                Human-readable terminal message.
            level:
                Optional presentation-level override.
            attributes:
                Optional terminal record attributes.
            exception:
                Optional exception to attach despite successful completion.
            exceptionAttributes:
                Optional catcher-owned attributes for exception capture.
            includeExceptionStack:
                Optional per-capture stack inclusion override.
            label:
                Optional record-local terminal label where the active span type
                permits explicit overrides.
            causedBy:
                Causal or logical evidence references.

        Returns:
            Event identifier of the terminal span record.

        Raises:
            TraceSpanStateError:
                If the span cannot be manually ended in its current lifecycle
                state.
            TraceSpanOwnershipError:
                If the caller does not own the span lifecycle.
            TraceContextError:
                If the span's ambient context lease is no longer valid or
                cannot be restored from the current execution context.

        """
        return self._endManual(
            "completed",
            message=message,
            level=level,
            attributes=attributes,
            exception=exception,
            exceptionAttributes=exceptionAttributes,
            includeExceptionStack=includeExceptionStack,
            label=label,
            causedBy=causedBy,
        )

    def fail(
        self,
        *,
        message: str = "",
        level: TraceLevel | None = None,
        attributes: Mapping[object, object] | None = None,
        exception: BaseException | None = None,
        exceptionAttributes: Mapping[object, object] | None = None,
        includeExceptionStack: bool | None = None,
        label: str | None = None,
        causedBy: tuple[TraceReferenceInput, ...] = (),
    ) -> TraceEventId:
        """
        Ends the span with the standard failed outcome.

        Args:
            message:
                Human-readable terminal message.
            level:
                Optional presentation-level override.
            attributes:
                Optional terminal record attributes.
            exception:
                Optional exception to attach.
            exceptionAttributes:
                Optional catcher-owned attributes for exception capture.
            includeExceptionStack:
                Optional per-capture stack inclusion override.
            label:
                Optional record-local terminal label where permitted.
            causedBy:
                Causal or logical evidence references.

        Returns:
            Event identifier of the terminal span record.

        Raises:
            TraceSpanStateError:
                If the span cannot be manually ended in its current lifecycle
                state.
            TraceSpanOwnershipError:
                If the caller does not own the span lifecycle.
            TraceContextError:
                If the span's ambient context lease is no longer valid or
                cannot be restored from the current execution context.

        """
        return self._endManual(
            "failed",
            message=message,
            level=level,
            attributes=attributes,
            exception=exception,
            exceptionAttributes=exceptionAttributes,
            includeExceptionStack=includeExceptionStack,
            label=label,
            causedBy=causedBy,
        )

    def error(
        self,
        err: BaseException,
        *,
        message: str = "",
        level: TraceLevel | None = None,
        attributes: Mapping[object, object] | None = None,
        exceptionAttributes: Mapping[object, object] | None = None,
        includeExceptionStack: bool | None = None,
        label: str | None = None,
        causedBy: tuple[TraceReferenceInput, ...] = (),
    ) -> TraceEventId:
        """
        Ends the span as errored and snapshots the supplied exception.

        Args:
            err:
                Exception attached to terminal span evidence.
            message:
                Human-readable terminal message.
            level:
                Optional presentation-level override.
            attributes:
                Optional terminal record attributes.
            exceptionAttributes:
                Optional catcher-owned attributes for exception capture.
            includeExceptionStack:
                Optional per-capture stack inclusion override.
            label:
                Optional record-local terminal label where permitted.
            causedBy:
                Causal or logical evidence references.

        Returns:
            Event identifier of the terminal span record.

        Raises:
            TypeError:
                If err is not a BaseException.
            TraceSpanStateError:
                If the span cannot be manually ended in its current lifecycle
                state.
            TraceSpanOwnershipError:
                If the caller does not own the span lifecycle.
            TraceContextError:
                If the span's ambient context lease is no longer valid or
                cannot be restored from the current execution context.

        """
        requireInstance(err, BaseException, "err")
        return self._endManual(
            "errored",
            message=message,
            level=level,
            attributes=attributes,
            exception=err,
            exceptionAttributes=exceptionAttributes,
            includeExceptionStack=includeExceptionStack,
            label=label,
            causedBy=causedBy,
        )

    def cancel(
        self,
        *,
        message: str = "",
        level: TraceLevel | None = None,
        attributes: Mapping[object, object] | None = None,
        exception: BaseException | None = None,
        exceptionAttributes: Mapping[object, object] | None = None,
        includeExceptionStack: bool | None = None,
        label: str | None = None,
        causedBy: tuple[TraceReferenceInput, ...] = (),
    ) -> TraceEventId:
        """
        Ends the span with the standard cancelled outcome.

        Args:
            message:
                Human-readable terminal message.
            level:
                Optional presentation-level override.
            attributes:
                Optional terminal record attributes.
            exception:
                Optional cancellation-related exception to attach.
            exceptionAttributes:
                Optional catcher-owned attributes for exception capture.
            includeExceptionStack:
                Optional per-capture stack inclusion override.
            label:
                Optional record-local terminal label where permitted.
            causedBy:
                Causal or logical evidence references.

        Returns:
            Event identifier of the terminal span record.

        Raises:
            TraceSpanStateError:
                If the span cannot be manually ended in its current lifecycle
                state.
            TraceSpanOwnershipError:
                If the caller does not own the span lifecycle.
            TraceContextError:
                If the span's ambient context lease is no longer valid or
                cannot be restored from the current execution context.

        """
        return self._endManual(
            "cancelled",
            message=message,
            level=level,
            attributes=attributes,
            exception=exception,
            exceptionAttributes=exceptionAttributes,
            includeExceptionStack=includeExceptionStack,
            label=label,
            causedBy=causedBy,
        )

    def end(
        self,
        outcome: str,
        *,
        message: str = "",
        level: TraceLevel | None = None,
        attributes: Mapping[object, object] | None = None,
        exception: BaseException | None = None,
        exceptionAttributes: Mapping[object, object] | None = None,
        includeExceptionStack: bool | None = None,
        label: str | None = None,
        causedBy: tuple[TraceReferenceInput, ...] = (),
    ) -> TraceEventId:
        """
        Ends the span with a declared or default-type custom outcome.

        Args:
            outcome:
                Undotted terminal outcome name.
            message:
                Human-readable terminal message.
            level:
                Optional presentation-level override.
            attributes:
                Optional terminal record attributes.
            exception:
                Optional exception to attach.
            exceptionAttributes:
                Optional catcher-owned attributes for exception capture.
            includeExceptionStack:
                Optional per-capture stack inclusion override.
            label:
                Optional record-local terminal label where permitted.
            causedBy:
                Causal or logical evidence references.

        Returns:
            Event identifier of the terminal span record.

        Raises:
            TraceUnknownOutcomeError:
                If an explicitly declared span type does not define outcome.
            TraceExplicitTypeOverrideError:
                If label attempts to override an explicitly declared outcome
                label.
            TraceSpanStateError:
                If the span cannot be manually ended in its current lifecycle
                state.
            TraceSpanOwnershipError:
                If the caller does not own the span lifecycle.
            TraceContextError:
                If the span's ambient context lease is no longer valid or
                cannot be restored from the current execution context.

        """
        return self._endManual(
            outcome,
            message=message,
            level=level,
            attributes=attributes,
            exception=exception,
            exceptionAttributes=exceptionAttributes,
            includeExceptionStack=includeExceptionStack,
            label=label,
            causedBy=causedBy,
        )

    def __enter__(self) -> Self:
        """
        Activates managed terminal handling for this span.

        Returns:
            This ActiveTraceSpan.

        Raises:
            TraceSpanStateError:
                If the span is already managed, ended, abandoned, or is not
                the current ambient span.
            TraceSpanOwnershipError:
                If called from a non-owner execution context.
            TraceContextError:
                If the span's ambient context lease is no longer valid for
                the current execution context.

        """
        self._requireCurrentOwner()
        if self._managed:
            raise TraceSpanStateError(
                "Trace span is already context-managed.",
            )
        self._managed = True
        return self

    def __exit__(
        self,
        exceptionType: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """
        Finalizes a synchronously managed span.

        Normal exit completes the span. CancelledError produces cancelled
        terminal evidence, while other escaping exceptions produce errored
        terminal evidence.

        Returns:
            False so an exception from the managed body is never suppressed.

        """
        self._finishManaged(exception)
        return False

    async def __aenter__(self) -> Self:
        """
        Activates asynchronous managed terminal handling.

        Returns:
            This ActiveTraceSpan.

        Raises:
            TraceSpanStateError:
                If the span is already managed, ended, abandoned, or is not
                the current ambient span.
            TraceSpanOwnershipError:
                If called from a non-owner execution context.
            TraceContextError:
                If the span's ambient context lease is no longer valid for
                the current execution context.

        """
        return self.__enter__()

    async def __aexit__(
        self,
        exceptionType: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """
        Finalizes an asynchronously managed span.

        Returns:
            False so an exception from the managed body is never suppressed.

        """
        self._finishManaged(exception)
        return False

    def _finishManaged(self, exception: BaseException | None) -> None:
        """
        Produces terminal evidence for managed exit or recovers by abandonment.

        Finalization failures are reported through the tracer emergency path.
        Recovery delegates to the tracer because the tracer owns the complete
        ambient span-owner mapping and can unwind descendant spans in LIFO
        order when necessary.
        """
        try:
            if exception is None:
                self._end(
                    "completed",
                    message="",
                    level=None,
                    attributes=None,
                    exception=None,
                    exceptionAttributes=None,
                    includeExceptionStack=None,
                    label=None,
                    causedBy=(),
                )
            elif isinstance(exception, asyncio.CancelledError):
                self._end(
                    "cancelled",
                    message="",
                    level=None,
                    attributes=None,
                    exception=exception,
                    exceptionAttributes=None,
                    includeExceptionStack=None,
                    label=None,
                    causedBy=(),
                )
            else:
                self._end(
                    "errored",
                    message="",
                    level=None,
                    attributes=None,
                    exception=exception,
                    exceptionAttributes=None,
                    includeExceptionStack=None,
                    label=None,
                    causedBy=(),
                )
        except Exception as finalizationError:  # noqa: BLE001
            self._tracer._reportManagedSpanFinalizationFailure(
                self,
                finalizationError,
            )
            try:
                self._tracer._abandonActiveSpan(
                    self,
                    restoreIfOwned=True,
                )
            except Exception as abandonmentError:  # noqa: BLE001
                self._tracer._reportManagedSpanFinalizationFailure(
                    self,
                    abandonmentError,
                )

    def _endManual(
        self,
        outcome: str,
        *,
        message: str,
        level: TraceLevel | None,
        attributes: Mapping[object, object] | None,
        exception: BaseException | None,
        exceptionAttributes: Mapping[object, object] | None,
        includeExceptionStack: bool | None,
        label: str | None,
        causedBy: tuple[TraceReferenceInput, ...],
    ) -> TraceEventId:
        """
        Applies the manual-lifecycle restriction and delegates termination.

        Context-managed spans cannot be manually ended because their context
        manager owns terminal outcome selection.

        Raises:
            TraceContextError:
                If the span's ambient context lease is no longer valid or
                cannot be restored from the current execution context.
        """
        if self._managed:
            raise TraceSpanStateError(
                "Context-managed trace span cannot be ended manually.",
            )

        return self._end(
            outcome,
            message=message,
            level=level,
            attributes=attributes,
            exception=exception,
            exceptionAttributes=exceptionAttributes,
            includeExceptionStack=includeExceptionStack,
            label=label,
            causedBy=causedBy,
        )

    def _end(
        self,
        outcome: str,
        *,
        message: str,
        level: TraceLevel | None,
        attributes: Mapping[object, object] | None,
        exception: BaseException | None,
        exceptionAttributes: Mapping[object, object] | None,
        includeExceptionStack: bool | None,
        label: str | None,
        causedBy: tuple[TraceReferenceInput, ...],
    ) -> TraceEventId:
        """
        Materializes, commits, and publishes terminal span evidence.

        Terminal record materialization occurs before ambient-context and
        lifecycle mutation. Once a valid terminal record exists, this span's
        context lease is restored, lifecycle ownership is removed, and the
        record is published.

        Raises:
            TraceContextError:
                If the span's ambient context lease is no longer valid or
                cannot be restored from the current execution context.

        """
        self._tracer._requireEmissionAllowed()
        self._requireCurrentOwner()

        cleanOutcome = requireOutcomeName(outcome)
        cleanMessage = requireString(message, "message")
        generated, recordType = self._resolveTerminalType(
            cleanOutcome,
            label,
        )
        resolvedLevel = (
            generated.level
            if level is None
            else requireTraceLevel(level)
        )
        exceptionSnapshot = self._tracer._captureException(
            exception,
            exceptionAttributes=exceptionAttributes,
            includeExceptionStack=includeExceptionStack,
        )
        record = self._tracer._createSpanEndRecord(
            activeSpan=self,
            recordType=recordType,
            level=resolvedLevel,
            message=cleanMessage,
            attributes=attributes,
            exceptionSnapshot=exceptionSnapshot,
            outcome=cleanOutcome,
            causedBy=causedBy,
        )

        self._parentLease.restore()
        self._ended = True
        self._tracer._finishActiveSpan(self)
        self._tracer._publishRecord(record)
        return record.eventId

    def _resolveTerminalType(
        self,
        outcome: str,
        label: str | None,
    ) -> tuple[TraceGeneratedType, str]:
        """
        Resolves generated presentation metadata and concrete terminal type.

        Explicitly declared span types restrict outcomes and labels to their
        portable definition. The default TRACE_SPAN type permits record-local
        custom outcomes and labels without creating new portable definitions.
        """
        generated = self._traceType.getOutcome(outcome)
        isDefault = (
            self._definition.traceTypeDefinitionId
            == TRACE_SPAN.getDefinition().traceTypeDefinitionId
        )

        if generated is None:
            if not isDefault:
                raise TraceUnknownOutcomeError(
                    f"Trace span type {self._traceType.name!r} does not "
                    f"declare outcome {outcome!r}.",
                )

            resolvedLabel = (
                outcome
                if label is None
                else requireOutcomeName(label, "label")
            )
            generated = TraceGeneratedType(
                label=resolvedLabel,
                level="info",
            )
            return generated, f"{self._definition.name}.{resolvedLabel}"

        if label is not None:
            if not isDefault:
                raise TraceExplicitTypeOverrideError(
                    "Declared TraceSpanType outcome labels "
                    "cannot be overridden per record.",
                )

            resolvedLabel = requireOutcomeName(label, "label")
            generated = TraceGeneratedType(
                label=resolvedLabel,
                level=generated.level,
                displayName=generated.displayName,
            )
            return generated, f"{self._definition.name}.{resolvedLabel}"

        return generated, f"{self._definition.name}.{generated.label}"

    def _isCurrentOwner(self) -> bool:
        """
        Returns whether the current execution context owns this span.

        Both thread identity and asyncio task identity must match the context
        in which the span lifecycle owner was created.
        """
        return (
            threading.get_ident() == self._ownerThreadId
            and _currentAsyncioTask() is self._ownerTask
        )

    def _requireCurrentOwner(self) -> None:
        """
        Requires an active, owned, top-of-stack span lifecycle.

        Raises:
            TraceSpanStateError:
                If the span has ended, was abandoned, or is not the current
                ambient span.
            TraceSpanOwnershipError:
                If the current thread or asyncio task does not own the span.
            TraceContextError:
                If the span's ambient context lease is no longer active or
                is not owned by the current execution context.

        """
        if self._ended:
            raise TraceSpanStateError("Trace span already ended.")

        if self._abandoned:
            raise TraceSpanStateError("Trace span was abandoned.")

        if threading.get_ident() != self._ownerThreadId:
            raise TraceSpanOwnershipError(
                "Trace span can only be ended by its owner thread.",
            )

        if _currentAsyncioTask() is not self._ownerTask:
            raise TraceSpanOwnershipError(
                "Trace span can only be ended by its owner task.",
            )

        self._parentLease.requireOwner()

        current = self._tracer._getCurrentSpanContext()
        if current != self._spanContext:
            raise TraceSpanStateError(
                "Trace span is not the active ambient "
                "span in its owner context.",
            )

    def _abandon(
        self,
        *,
        restoreIfOwned: bool,
    ) -> None:
        """
        Abandons this lifecycle owner and optionally restores its context
        lease.

        When restoration is requested from the owning execution context, this
        span must be the current ambient span. Descendant-span recovery is
        deliberately not performed here; Tracer owns that stack-level policy.

        Args:
            restoreIfOwned:
                Whether an owning caller should restore this span's context
                lease before marking the lifecycle abandoned.

        Raises:
            TraceSpanStateError:
                If owned restoration is requested while another span remains
                above this span in the ambient stack.
            TraceContextError:
                If the span's ambient context lease is no longer valid or
                cannot be restored from the current execution context.

        """
        if self._ended or self._abandoned:
            return

        if restoreIfOwned and self._isCurrentOwner():
            current = self._tracer._getCurrentSpanContext()
            if current != self._spanContext:
                raise TraceSpanStateError(
                    "Owned trace span cannot be abandoned while another "
                    "ambient span is above it.",
                )

            self._parentLease.restore()

        self._abandoned = True


def _currentAsyncioTask() -> asyncio.Task[object] | None:
    """
    Returns the current asyncio task without requiring a running event loop.

    Returns:
        The current task when called inside an active asyncio task, otherwise
        None.

    """
    with contextlib.suppress(RuntimeError):
        return cast(asyncio.Task[object] | None, asyncio.current_task())

    return None
