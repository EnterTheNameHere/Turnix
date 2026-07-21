# file: backend/core/tracing.py ; version 17
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import os
import pprint
import sys
import threading
import time
import traceback
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, Self, TextIO, cast

from backend.core.collections import immutableMapping
from backend.core.ids import uuidv7
from backend.core.validation import typeName

if TYPE_CHECKING:
    from types import TracebackType

type TraceLevel = Literal["debug", "info", "warning", "error", "critical"]
type TraceRecordKind = Literal["event", "spanBegin", "spanEnd"]
type TraceSpanOutcome = Literal["completed", "failed", "cancelled"]
type ConsoleColorMode = Literal["auto", "always", "never"]
type TraceParentMode = Literal["ambient", "explicit", "origin"]
type TraceScalar = None | bool | int | float | str
type TraceValue = (
    TraceScalar
    | tuple[TraceValue, ...]
    | Mapping[str, TraceValue]
)


_EMPTY_ATTRIBUTES: Mapping[str, TraceValue] = immutableMapping({})
_EMPTY_PARENT_EVENT_IDS: tuple[str, ...] = ()
_TRACE_LEVEL_RANKS: Mapping[TraceLevel, int] = immutableMapping(
    {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50},
)


def _newTracerId() -> str:
    return uuidv7(prefix="tracer_")


def _newTraceEventId() -> str:
    return uuidv7(prefix="traceEvent_")


def _newTraceSpanId() -> str:
    return uuidv7(prefix="traceSpan_")


def _requireNonBlankString(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string, not {typeName(value)}.")
    if not value.strip():
        raise ValueError(f"{name} must not be blank.")
    return value


def _requireTraceLevel(value: str) -> TraceLevel:
    if value not in _TRACE_LEVEL_RANKS:
        raise ValueError(
            "Trace level must be 'debug', 'info', "
            "'warning', 'error', or 'critical'.",
        )
    return cast(TraceLevel, value)


def _requireTraceRecordKind(value: str) -> TraceRecordKind:
    if value not in {"event", "spanBegin", "spanEnd"}:
        raise ValueError(
            "Trace record kind must be 'event', 'spanBegin', or 'spanEnd'.",
        )
    return cast(TraceRecordKind, value)


def _requireTraceSpanOutcome(value: str) -> TraceSpanOutcome:
    if value not in {"completed", "failed", "cancelled"}:
        raise ValueError(
            "Trace span outcome must be 'completed', 'failed', or 'cancelled'.",
        )
    return cast(TraceSpanOutcome, value)


def _requireTracerId(value: str) -> str:
    value = _requireNonBlankString(value, "tracerId")
    if not value.startswith("tracer_"):
        raise ValueError("tracerId must start with 'tracer_'.")
    return value


def _requireEventId(value: str) -> str:
    value = _requireNonBlankString(value, "eventId")
    if not value.startswith("traceEvent_"):
        raise ValueError("eventId must start with 'traceEvent_'.")
    return value


def _requireSpanId(value: str) -> str:
    value = _requireNonBlankString(value, "spanId")
    if not value.startswith("traceSpan_"):
        raise ValueError("spanId must start with 'traceSpan_'.")
    return value


def _requireExactBool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a bool, not {typeName(value)}.")
    return value


def _safeTypeName(value: object) -> str:
    with contextlib.suppress(Exception):
        return type(value).__name__
    return "<unknown type>"


def _safeExceptionTypeIdentity(err: BaseException) -> tuple[str, str, str]:
    """Return stable exception class identity with defensive fallbacks."""
    exceptionType = type(err)

    typeNameValue = "<unknown type>"
    typeQualifiedName = "<unknown qualified type>"
    typeModule = "<unknown module>"

    with contextlib.suppress(Exception):
        candidate = exceptionType.__name__
        if isinstance(candidate, str) and candidate:
            typeNameValue = candidate

    with contextlib.suppress(Exception):
        candidate = exceptionType.__qualname__
        if isinstance(candidate, str) and candidate:
            typeQualifiedName = candidate

    with contextlib.suppress(Exception):
        candidate = exceptionType.__module__
        if isinstance(candidate, str) and candidate:
            typeModule = candidate

    return (
        typeNameValue,
        typeQualifiedName,
        typeModule,
    )


def _safeString(
    value: object,
    *,
    fallback: str,
) -> str:
    with contextlib.suppress(Exception):
        return str(value)
    return fallback


def _currentAsyncioTask() -> asyncio.Task[object] | None:
    with contextlib.suppress(RuntimeError):
        return cast(asyncio.Task[object] | None, asyncio.current_task())
    return None


class TraceValueFreezer:
    """
    Convert supported trace values into recursively immutable values.

    Cycle detection and input-size limits are intentionally deferred so they can
    be added here later without changing TraceRecord semantics.
    """

    def freeze(self, value: object) -> TraceValue:
        return self._freeze(value, path="$")

    def freezeMapping(
        self,
        value: Mapping[str, object] | None,
    ) -> Mapping[str, TraceValue]:
        if value is None:
            return _EMPTY_ATTRIBUTES
        if not isinstance(value, Mapping):
            raise TypeError(
                "Trace attributes must be a mapping, "
                f"not {typeName(value)}.",
            )
        frozen = self.freeze(value)
        if not isinstance(frozen, Mapping):
            raise TypeError(
                "Frozen trace attributes unexpectedly stopped being a mapping.",
            )
        return cast(Mapping[str, TraceValue], frozen)

    def _freeze(
        self,
        value: object,
        *,
        path: str,
    ) -> TraceValue:
        if (value is None or isinstance(value, (bool, int, float, str))):
            return value

        if isinstance(value, Mapping):
            frozenMapping: dict[str, TraceValue] = {}
            for rawKey, rawValue in value.items():
                if not isinstance(rawKey, str):
                    raise TypeError(
                        f"Trace mapping key at {path} must be a string; "
                        f"received {typeName(rawKey)}.",
                    )
                frozenMapping[rawKey] = self._freeze(
                    rawValue,
                    path=f"{path}.{rawKey}",
                )
            return immutableMapping(frozenMapping)

        if isinstance(value, Sequence) and not isinstance(
            value,
            (str, bytes, bytearray),
        ):
            return tuple(
                self._freeze(
                    item,
                    path=f"{path}[{index}]",
                )
                for index, item in enumerate(value)
            )

        if isinstance(value, (set, frozenset)):
            raise TypeError(
                f"Trace value at {path} must not be a set. "
                "Convert it to a deterministically ordered sequence.",
            )

        if callable(value):
            raise TypeError(
                f"Trace value at {path} must not be callable; "
                f"received {typeName(value)}",
            )

        raise TypeError(
            f"Unsupported trace value at {path}: "
            f"{typeName(value)}.",
        )


@dataclass(frozen=True, slots=True)
class TraceParentContext:
    """Transferable immutable structural parent context."""

    tracerId: str
    spanId: str
    beginEventId: str

    def __post_init__(self) -> None:
        """Validate the immutable parent-context identity fields."""
        _requireTracerId(self.tracerId)
        _requireSpanId(self.spanId)
        _requireEventId(self.beginEventId)


@dataclass(frozen=True, slots=True)
class ExceptionSnapshot:
    """
    Immutable structured evidence captured from an exception.

    TraceRecordFactory is the sole normal constructor and recursively freezes
    attributes before construction. Direct construction is an internal trust
    boundary, matching TraceRecord.

    The module, qualified class name, and concise class name preserve exception
    identity for later storage and aggregation. Severity remains independent: a
    domain may attach ExceptionSnapshot to any record level according to its own
    semantics. A future store may deduplicate repeated stacks or introduce
    snapshot references without changing this value.
    """

    typeName: str
    typeQualifiedName: str
    typeModule: str
    message: str
    code: str | None = None
    stack: str | None = None
    attributes: Mapping[str, TraceValue] = (
        field(default_factory=lambda: _EMPTY_ATTRIBUTES)
    )

    def __post_init__(self) -> None:
        """Validate the fixed exception-snapshot fields without rebuilding attributes."""
        _requireNonBlankString(self.typeName, "exception typeName")
        _requireNonBlankString(
            self.typeQualifiedName,
            "exception typeQualifiedName",
        )
        _requireNonBlankString(self.typeModule, "exception typeModule")
        if not isinstance(self.message, str):
            raise TypeError(
                "Exception message must be a string, "
                f"not {typeName(self.message)}.",
            )
        if self.code is not None:
            _requireNonBlankString(self.code, "exception code")
        if (self.stack is not None and not isinstance(self.stack, str)):
            raise TypeError(
                "exception stack must be a string or None, "
                f"not {typeName(self.stack)}.",
            )
        if not isinstance(self.attributes, Mapping):
            raise TypeError(
                "exception attributes must be a mapping, "
                f"not {typeName(self.attributes)}.",
            )


@dataclass(frozen=True, slots=True)
class TraceRecord:
    """
    One immutable normalized trace record.

    Records emitted by Tracer are created through TraceRecordFactory. Their
    attributes and exception snapshots are expected to have already been
    recursively frozen. Direct construction is an internal trust boundary:
    supplying mutable nested values directly can violate practical immutability.

    Storage, DAG construction and validation, retention, purge evidence,
    controlled access, and export guarantees are intentionally separate future
    layers. Future graph readers must use explicit fields rather than infer edge
    meaning from parentEventIds ordering; its order is deterministic but does
    not define causal priority or edge type.
    """

    eventId: str
    timestampUnixNs: int
    kind: TraceRecordKind
    level: TraceLevel
    domain: str
    reason: str
    message: str
    attributes: Mapping[str, TraceValue] = field(
        default_factory=lambda: _EMPTY_ATTRIBUTES,
    )
    exception: ExceptionSnapshot | None = None
    containingSpanId: str | None = None
    containingSpanBeginEventId: str | None = None
    parentEventIds: tuple[str, ...] = _EMPTY_PARENT_EVENT_IDS
    spanId: str | None = None
    spanBeginEventId: str | None = None
    origin: str | None = None
    outcome: TraceSpanOutcome | None = None
    durationNs: int | None = None

    def __post_init__(self) -> None:
        """Validate local record invariants without rebuilding frozen data."""
        _requireEventId(self.eventId)

        if type(self.timestampUnixNs) is not int:
            raise TypeError(
                "timestampUnixNs must be an int, "
                f"not {typeName(self.timestampUnixNs)}.",
            )
        if self.timestampUnixNs < 0:
            raise ValueError(
                "timestampUnixNs must not be negative; "
                f"got {self.timestampUnixNs}.",
            )

        _requireTraceRecordKind(self.kind)
        _requireTraceLevel(self.level)
        _requireNonBlankString(self.domain, "domain")
        _requireNonBlankString(self.reason, "reason")

        if not isinstance(self.message, str):
            raise TypeError(
                "message must be a string, "
                f"not {typeName(self.message)}.",
            )

        if not isinstance(self.attributes, Mapping):
            raise TypeError(
                "attributes must be a mapping, "
                f"not {typeName(self.attributes)}.",
            )

        if (
            self.exception is not None
            and not isinstance(self.exception, ExceptionSnapshot)
        ):
            raise TypeError(
                "exception must be an ExceptionSnapshot or None, "
                f"not {typeName(self.exception)}.",
            )

        normalizedParentEventIds = tuple(self.parentEventIds)
        object.__setattr__(self, "parentEventIds", normalizedParentEventIds)
        for parentEventId in normalizedParentEventIds:
            _requireEventId(parentEventId)
        if len(normalizedParentEventIds) != len(set(normalizedParentEventIds)):
            raise ValueError("parentEventIds must not contain duplicates.")
        if self.eventId in normalizedParentEventIds:
            raise ValueError("Trace record must not list itself as a parent.")

        self._validateStructuralFields()
        if self.origin is not None:
            _requireNonBlankString(self.origin, "origin")

        if self.kind == "event":
            self._validateEventRecord()
        elif self.kind == "spanBegin":
            self._validateSpanBeginRecord()
        else:
            self._validateSpanEndRecord()

    def _validateStructuralFields(self) -> None:
        if (
            self.containingSpanId is None
            and self.containingSpanBeginEventId is None
        ):
            return
        if (
            self.containingSpanId is None
            or self.containingSpanBeginEventId is None
        ):
            raise ValueError(
                "containingSpanId and containingSpanBeginEventId must "
                "either both be present or both be absent.",
            )
        _requireSpanId(self.containingSpanId)
        _requireEventId(self.containingSpanBeginEventId)
        if self.containingSpanBeginEventId not in self.parentEventIds:
            raise ValueError(
                "A structurally contained record must include "
                "containingSpanBeginEventId in parentEventIds.",
            )

    def _validateEventRecord(self) -> None:
        if self.spanId is not None:
            raise ValueError("Event record must not contain spanId.")
        if self.spanBeginEventId is not None:
            raise ValueError("Event record must not contain spanBeginEventId.")
        if self.outcome is not None:
            raise ValueError("Event record must not contain outcome.")
        if self.durationNs is not None:
            raise ValueError("Event record must not contain durationNs.")
        self._validateStructuralOrigin()

    def _validateSpanBeginRecord(self) -> None:
        if self.spanId is None:
            raise ValueError("Span-begin record must contain spanId.")
        _requireSpanId(self.spanId)
        if self.spanBeginEventId != self.eventId:
            raise ValueError(
                "Span-begin record spanBeginEventId must equal eventId.",
            )
        if self.containingSpanId == self.spanId:
            raise ValueError("Span-begin record must not contain itself.")
        if self.outcome is not None:
            raise ValueError("Span-begin record must not contain outcome.")
        if self.durationNs is not None:
            raise ValueError("Span-begin record must not contain durationNs.")
        self._validateStructuralOrigin()

    def _validateSpanEndRecord(self) -> None:
        if self.spanId is None:
            raise ValueError("Span-end record must contain spanId.")
        _requireSpanId(self.spanId)
        if self.spanBeginEventId is None:
            raise ValueError("Span-end record must contain spanBeginEventId.")
        _requireEventId(self.spanBeginEventId)
        if self.spanBeginEventId not in self.parentEventIds:
            raise ValueError(
                "Span-end record must include spanBeginEventId in parentEventIds.",
            )
        if self.containingSpanId == self.spanId:
            raise ValueError("Span-end record must not contain itself.")
        if self.origin is not None:
            raise ValueError("Span-end record must not contain origin.")
        if self.outcome is None:
            raise ValueError("Span-end record must contain outcome.")
        _requireTraceSpanOutcome(self.outcome)
        if type(self.durationNs) is not int:
            raise TypeError(
                "Span-end durationNs must be an int, "
                f"not {typeName(self.durationNs)}.",
            )
        if self.durationNs < 0:
            raise ValueError(
                "Span-end durationNs must not be negative; "
                f"got {self.durationNs}.",
            )

    def _validateStructuralOrigin(self) -> None:
        if self.containingSpanId is None:
            if self.origin is None:
                raise ValueError(
                    "A structurally parent-less event or span-begin "
                    "record must contain an explicit origin.",
                )
            return
        if self.origin is not None:
            raise ValueError(
                "A structurally contained record "
                "must not contain origin.",
            )


class TraceRecordFactory:
    """
    Create normalized immutable trace records with generated identity and time.

    captureException() is the normal ExceptionSnapshot construction path. A
    caller-supplied snapshot is trusted as already normalized, matching the
    direct-construction trust boundary of TraceRecord and ExceptionSnapshot.
    """

    def __init__(
        self,
        *,
        freezer: TraceValueFreezer | None = None,
        includeExceptionStacks: bool = True,
    ) -> None:
        self._freezer = freezer if freezer is not None else TraceValueFreezer()
        self._includeExceptionStacks = _requireExactBool(
            includeExceptionStacks,
            "includeExceptionStacks",
        )

    def newSpanId(self) -> str:
        return _newTraceSpanId()

    def freezeAttributes(
        self,
        attributes: Mapping[str, object] | None,
    ) -> Mapping[str, TraceValue]:
        return self._freezer.freezeMapping(attributes)

    def captureException(
        self,
        err: BaseException,
        *,
        code: str | None = None,
        includeStack: bool | None = None,
    ) -> ExceptionSnapshot:
        if not isinstance(err, BaseException):
            raise TypeError(f"err must be an exception, not {typeName(err)}.")
        if includeStack is not None and type(includeStack) is not bool:
            raise TypeError(
                "includeStack must be a bool or None, "
                f"not {typeName(includeStack)}.",
            )

        resolvedIncludeStack = (
            self._includeExceptionStacks
            if includeStack is None
            else includeStack
        )
        stack: str | None = None
        exceptionAttributes: dict[str, object] = {}
        if resolvedIncludeStack:
            try:
                stack = "".join(
                    traceback.format_exception(
                        type(err),
                        err,
                        err.__traceback__,
                    ),
                )
            except Exception as formattingError:  # noqa: BLE001
                exceptionAttributes["stackUnavailable"] = True
                exceptionAttributes["stackFormattingErrorType"] = (
                    _safeTypeName(formattingError)
                )

        (
            exceptionTypeName,
            exceptionTypeQualifiedName,
            exceptionTypeModule,
        ) = _safeExceptionTypeIdentity(err)

        return ExceptionSnapshot(
            typeName=exceptionTypeName,
            typeQualifiedName=exceptionTypeQualifiedName,
            typeModule=exceptionTypeModule,
            message=_safeString(
                err,
                fallback="<exception message unavailable>",
            ),
            code=(
                None
                if code is None
                else _requireNonBlankString(code, "exception code")
            ),
            stack=stack,
            attributes=self.freezeAttributes(exceptionAttributes),
        )

    def createEvent(
        self,
        *,
        level: TraceLevel,
        domain: str,
        reason: str,
        message: str,
        attributes: Mapping[str, object] | None,
        exception: ExceptionSnapshot | None,
        structuralParent: TraceParentContext | None,
        origin: str | None,
        causalParentEventIds: tuple[str, ...],
    ) -> TraceRecord:
        return self._createRecord(
            kind="event",
            level=level,
            domain=domain,
            reason=reason,
            message=message,
            attributes=self.freezeAttributes(attributes),
            exception=exception,
            structuralParent=structuralParent,
            parentEventIds=self._composeParentEventIds(
                structuralParent=structuralParent,
                requiredParentEventIds=(),
                causalParentEventIds=causalParentEventIds,
            ),
            spanId=None,
            spanBeginEventId=None,
            origin=origin,
            outcome=None,
            durationNs=None,
        )

    def createSpanBegin(
        self,
        *,
        spanId: str,
        level: TraceLevel,
        domain: str,
        reason: str,
        message: str,
        attributes: Mapping[str, object] | None,
        exception: ExceptionSnapshot | None,
        structuralParent: TraceParentContext | None,
        origin: str | None,
        causalParentEventIds: tuple[str, ...],
    ) -> TraceRecord:
        eventId = _newTraceEventId()
        return self._createRecord(
            eventId=eventId,
            kind="spanBegin",
            level=level,
            domain=domain,
            reason=reason,
            message=message,
            attributes=self.freezeAttributes(attributes),
            exception=exception,
            structuralParent=structuralParent,
            parentEventIds=self._composeParentEventIds(
                structuralParent=structuralParent,
                requiredParentEventIds=(),
                causalParentEventIds=causalParentEventIds,
            ),
            spanId=spanId,
            spanBeginEventId=eventId,
            origin=origin,
            outcome=None,
            durationNs=None,
        )

    def createSpanEnd(
        self,
        *,
        spanId: str,
        spanBeginEventId: str,
        level: TraceLevel,
        domain: str,
        reason: str,
        message: str,
        attributes: Mapping[str, object] | None,
        exception: ExceptionSnapshot | None,
        structuralParent: TraceParentContext | None,
        outcome: TraceSpanOutcome,
        durationNs: int,
        causalParentEventIds: tuple[str, ...],
    ) -> TraceRecord:
        return self._createRecord(
            kind="spanEnd",
            level=level,
            domain=domain,
            reason=reason,
            message=message,
            attributes=self.freezeAttributes(attributes),
            exception=exception,
            structuralParent=structuralParent,
            parentEventIds=self._composeParentEventIds(
                structuralParent=structuralParent,
                requiredParentEventIds=(spanBeginEventId,),
                causalParentEventIds=causalParentEventIds,
            ),
            spanId=spanId,
            spanBeginEventId=spanBeginEventId,
            origin=None,
            outcome=outcome,
            durationNs=durationNs,
        )

    def _composeParentEventIds(
        self,
        *,
        structuralParent: TraceParentContext | None,
        requiredParentEventIds: tuple[str, ...],
        causalParentEventIds: tuple[str, ...],
    ) -> tuple[str, ...]:
        parentEventIds: list[str] = []
        for eventId in requiredParentEventIds:
            cleanEventId = _requireEventId(eventId)
            if cleanEventId not in parentEventIds:
                parentEventIds.append(cleanEventId)
        if (
            structuralParent is not None
            and structuralParent.beginEventId not in parentEventIds
        ):
            parentEventIds.append(structuralParent.beginEventId)
        for eventId in causalParentEventIds:
            cleanEventId = _requireEventId(eventId)
            if cleanEventId not in parentEventIds:
                parentEventIds.append(cleanEventId)
        return tuple(parentEventIds)

    def _createRecord(
        self,
        *,
        kind: TraceRecordKind,
        level: TraceLevel,
        domain: str,
        reason: str,
        message: str,
        attributes: Mapping[str, TraceValue],
        exception: ExceptionSnapshot | None,
        structuralParent: TraceParentContext | None,
        parentEventIds: tuple[str, ...],
        spanId: str | None,
        spanBeginEventId: str | None,
        origin: str | None,
        outcome: TraceSpanOutcome | None,
        durationNs: int | None,
        eventId: str | None = None,
    ) -> TraceRecord:
        if not isinstance(message, str):
            raise TypeError(
                f"message must be a string, not {typeName(message)}.",
            )
        return TraceRecord(
            eventId=_newTraceEventId() if eventId is None else eventId,
            timestampUnixNs=time.time_ns(),
            kind=_requireTraceRecordKind(kind),
            level=_requireTraceLevel(level),
            domain=_requireNonBlankString(domain, "domain"),
            reason=_requireNonBlankString(reason, "reason"),
            message=message,
            attributes=attributes,
            exception=exception,
            containingSpanId=None if structuralParent is None else structuralParent.spanId,
            containingSpanBeginEventId=None if structuralParent is None else structuralParent.beginEventId,
            parentEventIds=parentEventIds,
            spanId=spanId,
            spanBeginEventId=spanBeginEventId,
            origin=origin,
            outcome=outcome,
            durationNs=durationNs,
        )


@dataclass(slots=True)
class _TracePublicationState:
    """Share publication activity across copied execution contexts."""

    active: bool
    threadId: int
    task: asyncio.Task[object] | None


class TraceRuntimeContext:
    """
    Own execution-context-local parentage and publication state.

    A future active-span registry may attach here to report abandoned
    non-lexical spans. Such monitoring must not redefine existing record
    relationships.
    """

    def __init__(self) -> None:
        self._tracerId: str | None = None
        self._bindingLock = threading.Lock()
        self._currentParentVar: contextvars.ContextVar[TraceParentContext | None] = (
            contextvars.ContextVar(
                f"actant_current_trace_parent_{id(self)}",
                default=None,
            )
        )
        self._publicationStateVar: contextvars.ContextVar[_TracePublicationState | None] = (
            contextvars.ContextVar(
                f"actant_trace_publication_state_{id(self)}",
                default=None,
            )
        )

    def bindTracer(self, tracerId: str) -> None:
        """Bind this runtime context permanently and thread-safely to one tracer."""
        tracerId = _requireTracerId(tracerId)

        with self._bindingLock:
            if self._tracerId is None:
                self._tracerId = tracerId
                return

            if self._tracerId != tracerId:
                raise RuntimeError(
                    "TraceRuntimeContext is already bound to a different tracer.",
                )

    def _requireBoundTracerId(self) -> str:
        tracerId = self._tracerId
        if tracerId is None:
            raise RuntimeError(
                "TraceRuntimeContext must be bound to a tracer before use.",
            )
        return tracerId

    def currentParent(self) -> TraceParentContext | None:
        return self._currentParentVar.get()

    def requireNormalTracingAllowed(self) -> None:
        """Reject normal tracing recursively invoked by this publication owner."""
        state = self._publicationStateVar.get()

        if (
            state is not None
            and state.active
            and state.threadId == threading.get_ident()
            and state.task is _currentAsyncioTask()
        ):
            raise RuntimeError(
                "Normal tracing must not be invoked recursively by trace publication.",
            )

    def installParent(self, parent: TraceParentContext) -> _TraceParentLease:
        if not isinstance(parent, TraceParentContext):
            raise TypeError(
                f"parent must be a TraceParentContext, not {typeName(parent)}.",
            )
        if parent.tracerId != self._requireBoundTracerId():
            raise RuntimeError(
                "Trace parent belongs to a different runtime-context tracer.",
            )
        return _TraceParentLease(
            _contextVar=self._currentParentVar,
            _token=self._currentParentVar.set(parent),
            _ownerThreadId=threading.get_ident(),
            _ownerTask=_currentAsyncioTask(),
        )

    def tryEnterPublication(self) -> _TracePublicationLease | None:
        currentThreadId = threading.get_ident()
        currentTask = _currentAsyncioTask()
        state = self._publicationStateVar.get()

        if (
            state is not None
            and state.active
            and state.threadId == currentThreadId
            and state.task is currentTask
        ):
            return None

        publicationState = _TracePublicationState(
            active=True,
            threadId=currentThreadId,
            task=currentTask,
        )

        return _TracePublicationLease(
            _contextVar=self._publicationStateVar,
            _token=self._publicationStateVar.set(publicationState),
            _state=publicationState,
            _ownerThreadId=currentThreadId,
            _ownerTask=currentTask,
        )


class TraceContext:
    """
    Provide read-only access to transferable ambient trace context.

    The returned TraceParentContext is an immutable snapshot of the structural
    parent current at access time. It may be transferred across threads or
    callbacks and may intentionally outlive the active span that produced it.
    It represents logical structural parentage, not proof that the source span
    is still active.

    Async tasks may inherit ambient context through Python context propagation.
    Detached work should deliberately use an origin and explicit causal links
    instead of relying on inherited structural parentage. Ambient mutation and
    publication state remain private to TraceRuntimeContext.
    """

    __slots__ = ("_runtimeContext", "_tracerId")

    def __init__(
        self,
        *,
        tracerId: str,
        runtimeContext: TraceRuntimeContext,
    ) -> None:
        self._tracerId = _requireTracerId(tracerId)

        if not isinstance(runtimeContext, TraceRuntimeContext):
            raise TypeError(
                f"runtimeContext must be a TraceRuntimeContext, "
                f"not {typeName(runtimeContext)}.",
            )

        self._runtimeContext = runtimeContext

    @property
    def currentParent(self) -> TraceParentContext | None:
        """Return the current transferable structural parent, if one exists."""
        parent = self._runtimeContext.currentParent()

        if parent is not None and parent.tracerId != self._tracerId:
            raise RuntimeError(
                "Ambient trace parent belongs to a different tracer.",
            )

        return parent

    def requireCurrentParent(self) -> TraceParentContext:
        """Return the current structural parent or reject missing context."""
        parent = self.currentParent
        if parent is None:
            raise RuntimeError(
                "The current execution context has no structural trace parent.",
            )
        return parent


@dataclass(slots=True)
class _TraceParentLease:
    """Own one installed ambient parent and restore it exactly once."""

    _contextVar: contextvars.ContextVar[TraceParentContext | None]
    _token: contextvars.Token[TraceParentContext | None]
    _ownerThreadId: int
    _ownerTask: asyncio.Task[object] | None
    _restored: bool = False

    def requireOwner(self) -> None:
        if self._restored:
            raise RuntimeError("Trace parent lease has already been restored.")
        if threading.get_ident() != self._ownerThreadId:
            raise RuntimeError(
                "Trace parent lease must be restored "
                "from the thread that installed it.",
            )
        if _currentAsyncioTask() is not self._ownerTask:
            raise RuntimeError(
                "Trace parent lease must be restored "
                "from the async task that installed it.",
            )

    def restore(self) -> None:
        self.requireOwner()
        try:
            self._contextVar.reset(self._token)
        except BaseException as err:
            raise RuntimeError(
                "Trace parent lease could not restore its execution context.",
            ) from err
        self._restored = True


@dataclass(slots=True)
class _TracePublicationLease:
    """Own one context-local normal-publication guard."""

    _contextVar: contextvars.ContextVar[_TracePublicationState | None]
    _token: contextvars.Token[_TracePublicationState | None]
    _state: _TracePublicationState
    _ownerThreadId: int
    _ownerTask: asyncio.Task[object] | None
    _restored: bool = False

    def restore(self) -> None:
        if self._restored:
            raise RuntimeError(
                "Trace publication lease has already been restored.",
            )
        if threading.get_ident() != self._ownerThreadId:
            raise RuntimeError(
                "Trace publication lease must be restored "
                "from the thread that installed it.",
            )
        if _currentAsyncioTask() is not self._ownerTask:
            raise RuntimeError(
                "Trace publication lease must be restored "
                "from the async task that installed it.",
            )
        # Copied contexts retain this same state object. Deactivate it before
        # reset so delayed callbacks cannot remain permanently marked as
        # publishing, even if ContextVar.reset() itself fails.
        self._state.active = False

        try:
            self._contextVar.reset(self._token)
        except BaseException as err:
            raise RuntimeError(
                "Trace publication lease could not restore its execution context.",
            ) from err

        self._restored = True


class TraceDestination(Protocol):
    """
    Receives completed immutable trace records.

    write() may be called concurrently from multiple threads or execution
    contexts. A destination must provide any synchronization required by its
    own state. Ordinary destination failures are isolated per destination;
    process-control exceptions stop publication and propagate immediately.
    """

    def write(self, record: TraceRecord) -> None:
        ...


class TraceEmergencyReporter:
    """
    Report trace-infrastructure failures without normal tracing.

    Ordinary reporting failures are suppressed. Process-control exceptions are
    allowed to propagate.
    """

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
    ) -> None:
        self._stream = sys.stderr if stream is None else stream
        self._writeLock = threading.Lock()

    def reportNoDestinations(self) -> None:
        self._write(
            "[ACTANT TRACE WARNING] No trace destinations are configured; "
            "emitted records will be discarded.",
        )

    def reportRecursivePublication(self, record: TraceRecord) -> None:
        self._write(
            "[ACTANT TRACE WARNING] "
            "Recursive trace publication was "
            f"ignored for {record.eventId}.",
        )

    def reportDestinationFailure(
        self,
        *,
        destination: TraceDestination,
        record: TraceRecord,
        err: BaseException,
    ) -> None:
        self._write(
            "[ACTANT TRACE FAILURE] "
            f"{_safeTypeName(destination)} raised {_safeTypeName(err)} "
            f"while writing {record.eventId}: "
            f"{_safeString(err, fallback='<exception message unavailable>')}.",
        )

    def reportSpanFinalizationFailure(
        self,
        *,
        spanId: str,
        err: BaseException,
    ) -> None:
        self._write(
            "[ACTANT TRACE FAILURE] "
            f"Could not finalize managed span {spanId}: {_safeTypeName(err)}: "
            f"{_safeString(err, fallback='<exception message unavailable>')}.",
        )

    def reportPublicationRestorationFailure(
        self,
        *,
        record: TraceRecord,
        err: BaseException,
    ) -> None:
        self._write(
            "[ACTANT TRACE FAILURE] "
            "Could not restore trace publication context after "
            f"{record.eventId}: {_safeTypeName(err)}: "
            f"{_safeString(err, fallback='<exception message unavailable>')}.",
        )

    def _write(self, message: str) -> None:
        with contextlib.suppress(Exception), self._writeLock:
            print(message, file=self._stream, flush=True)


class TraceEmergencyChannel:
    """
    Prevent ordinary emergency-reporter failures from escaping into observed application behaviour.

    If the configured reporter raises Exception, a final direct-stderr fallback
    is attempted. BaseException is not caught, so process-control exceptions
    retain their normal behaviour.
    """

    def __init__(
        self,
        *,
        reporter: TraceEmergencyReporter,
        fallbackStream: TextIO | None = None,
    ) -> None:
        self._reporter = reporter
        self._fallbackStream = (
            sys.stderr
            if fallbackStream is None
            else fallbackStream
        )
        self._fallbackLock = threading.Lock()

    def reportNoDestinations(self) -> None:
        self._invoke(
            methodName="reportNoDestinations",
            fallbackMessage=(
                "[ACTANT TRACE WARNING] "
                "No trace destinations are configured; "
                "emitted records will be discarded."
            ),
        )

    def reportRecursivePublication(self, record: TraceRecord) -> None:
        self._invoke(
            methodName="reportRecursivePublication",
            args=(record,),
            fallbackMessage=(
                "[ACTANT TRACE WARNING] "
                "Recursive trace publication was ignored for "
                f"{record.eventId}."
            ),
        )

    def reportDestinationFailure(
        self,
        *,
        destination: TraceDestination,
        record: TraceRecord,
        err: BaseException,
    ) -> None:
        self._invoke(
            methodName="reportDestinationFailure",
            kwargs={
                "destination": destination,
                "record": record,
                "err": err,
            },
            fallbackMessage=(
                "[ACTANT TRACE FAILURE] "
                f"{_safeTypeName(destination)} raised {_safeTypeName(err)} "
                f"while writing {record.eventId}."
            ),
        )

    def reportSpanFinalizationFailure(
        self,
        *,
        spanId: str,
        err: BaseException,
    ) -> None:
        self._invoke(
            methodName="reportSpanFinalizationFailure",
            kwargs={
                "spanId": spanId,
                "err": err,
            },
            fallbackMessage=(
                "[ACTANT TRACE FAILURE] "
                f"Could not finalize managed span {spanId}: "
                f"{_safeTypeName(err)}."
            ),
        )

    def reportPublicationRestorationFailure(
        self,
        *,
        record: TraceRecord,
        err: BaseException,
    ) -> None:
        self._invoke(
            methodName="reportPublicationRestorationFailure",
            kwargs={
                "record": record,
                "err": err,
            },
            fallbackMessage=(
                "[ACTANT TRACE FAILURE] "
                "Could not restore trace publication context after "
                f"{record.eventId}: {_safeTypeName(err)}."
            ),
        )

    def _invoke(
        self,
        *,
        methodName: str,
        args: tuple[object, ...] = (),
        kwargs: Mapping[str, object] | None = None,
        fallbackMessage: str,
    ) -> None:
        try:
            method = getattr(
                self._reporter,
                methodName,
            )

            method(
                *args,
                **dict(kwargs or {}),
            )
        except Exception as reporterError:  # noqa: BLE001
            self._writeFallback(
                (
                    f"{fallbackMessage} "
                    "The configured emergency reporter also failed with "
                    f"{_safeTypeName(reporterError)}."
                ),
            )

    def _writeFallback(self, message: str) -> None:
        with contextlib.suppress(Exception), self._fallbackLock:
            print(
                message,
                file=self._fallbackStream,
                flush=True,
            )


class TracePublisher:
    """
    Deliver completed records and isolate destination failures.

    A future implementation may append to a TraceStore before distribution and
    then publish through a hub to subscribers. Retention and structured
    trace-loss evidence belong behind this seam, not in Tracer or
    TraceRecordFactory.
    """

    def __init__(
        self,
        *,
        destinations: Sequence[TraceDestination],
        runtimeContext: TraceRuntimeContext,
        emergencyChannel: TraceEmergencyChannel,
    ) -> None:
        if isinstance(destinations, (str, bytes, bytearray)):
            raise TypeError(
                "destinations must be a non-string sequence, "
                f"not {typeName(destinations)}.",
            )
        normalizedDestinations = tuple(destinations)
        for destination in normalizedDestinations:
            if not callable(getattr(destination, "write", None)):
                raise TypeError(
                    "Trace destination must provide callable "
                    f"write(), not {typeName(destination)}.",
                )
        self._destinations = normalizedDestinations
        self._runtimeContext = runtimeContext
        self._emergencyChannel = emergencyChannel
        if not self._destinations:
            self._emergencyChannel.reportNoDestinations()

    def publish(self, record: TraceRecord) -> None:
        publicationLease = self._runtimeContext.tryEnterPublication()

        if publicationLease is None:
            self._emergencyChannel.reportRecursivePublication(record)
            return

        try:
            for destination in self._destinations:
                try:
                    destination.write(record)
                except Exception as err:  # noqa: BLE001
                    self._emergencyChannel.reportDestinationFailure(
                        destination=destination,
                        record=record,
                        err=err,
                    )
        finally:
            try:
                publicationLease.restore()
            except BaseException as restorationError:  # noqa: BLE001
                self._emergencyChannel.reportPublicationRestorationFailure(
                    record=record,
                    err=restorationError,
                )


_ANSI_RESET = "\x1b[0m"
_ANSI_DIM = "\x1b[2m"
_ANSI_CYAN = "\x1b[36m"
_ANSI_YELLOW = "\x1b[33m"
_ANSI_BRIGHT_RED = "\x1b[91m"
_ANSI_BOLD_RED = "\x1b[1;91m"
_ANSI_GREEN = "\x1b[32m"


class ConsoleTraceDestination:
    """
    Prints colored, human-readable trace records to a text stream.

    The output is intended for live development diagnostics. It is not a
    serialization format and must not be parsed as authoritative trace data.

    Closed-parent history is deliberately bounded and approximate. Its
    annotations are rendering hints only; authoritative late/missing/purged
    classification belongs to a future graph/store layer.
    """

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        colorMode: ConsoleColorMode = "auto",
        minimumLevel: TraceLevel = "debug",
        showTimestamp: bool = True,
        showAttributes: bool = True,
        closedSpanHistoryLimit: int = 4096,
    ) -> None:
        if colorMode not in {"auto", "always", "never"}:
            raise ValueError("colorMode must be 'auto', 'always', or 'never'.")

        self._stream = sys.stdout if stream is None else stream
        self._colorMode = colorMode
        self._minimumLevel = _requireTraceLevel(minimumLevel)
        self._showTimestamp = _requireExactBool(showTimestamp, "showTimestamp")
        self._showAttributes = (
            _requireExactBool(showAttributes, "showAttributes")
        )
        if type(closedSpanHistoryLimit) is not int:
            raise TypeError(
                "closedSpanHistoryLimit must be an int, "
                f"not {typeName(closedSpanHistoryLimit)}.",
            )
        if closedSpanHistoryLimit < 0:
            raise ValueError(
                "closedSpanHistoryLimit must not be negative; "
                f"got {closedSpanHistoryLimit}.",
            )
        self._closedSpanHistoryLimit = closedSpanHistoryLimit
        self._writeLock = threading.Lock()
        self._spanDepthBySpanId: dict[str, int] = {}
        self._recentClosedSpanIds: OrderedDict[str, None] = OrderedDict()

    def write(self, record: TraceRecord) -> None:
        with self._writeLock:
            parentAnnotation = self._parentAnnotation(record)
            depth = self._deriveDepth(record)
            if self._shouldWrite(record.level):
                self._writeRecord(
                    record,
                    depth=depth,
                    parentAnnotation=parentAnnotation,
                )
            if record.kind == "spanEnd" and record.spanId is not None:
                self._closeSpan(record.spanId)

    def _writeRecord(
        self,
        record: TraceRecord,
        *,
        depth: int,
        parentAnnotation: str | None,
    ) -> None:
        colorEnabled = self._isColorEnabled()
        color = self._colorFor(record)
        symbol = self._symbolFor(record)
        indentation = "  " * depth
        timestampPrefix = ""

        if self._showTimestamp:
            timestampPrefix = (
                f"{self._formatTimestamp(record.timestampUnixNs)} "
            )

        line = (
            f"[ACTANT] {timestampPrefix}"
            f"{indentation}{symbol} "
            f"{record.domain} "
            f"{record.reason}"
        )

        if record.message:
            line += f" — {record.message}"
        if record.origin is not None:
            line += f" [origin={record.origin}]"
        if parentAnnotation is not None:
            line += f" [{parentAnnotation}]"
        if record.kind == "spanEnd":
            if type(record.durationNs) is int:
                durationMilliseconds = record.durationNs / 1_000_000
                line += f" [{record.outcome}, {durationMilliseconds:.3f} ms]"
            else:
                line += f" [{record.outcome}]"
        self._printLine(line, colorEnabled=colorEnabled, color=color)

        if record.exception is not None:
            self._writeNamedValue(
                name="exception",
                value=record.exception,
                depth=depth,
                colorEnabled=colorEnabled,
                color=color,
            )
        if self._showAttributes and record.attributes:
            for key, value in record.attributes.items():
                self._writeNamedValue(
                    name=key,
                    value=value,
                    depth=depth,
                    colorEnabled=colorEnabled,
                    color=color,
                )

    def _parentAnnotation(self, record: TraceRecord) -> str | None:
        containingSpanId = record.containingSpanId
        if containingSpanId is None:
            return None
        if containingSpanId in self._spanDepthBySpanId:
            return None
        if containingSpanId in self._recentClosedSpanIds:
            return "late from closed parent"
        return "parent not observed"

    def _closeSpan(self, spanId: str) -> None:
        self._spanDepthBySpanId.pop(spanId, None)
        if self._closedSpanHistoryLimit == 0:
            return
        self._recentClosedSpanIds.pop(spanId, None)
        self._recentClosedSpanIds[spanId] = None
        while len(self._recentClosedSpanIds) > self._closedSpanHistoryLimit:
            self._recentClosedSpanIds.popitem(last=False)

    def _deriveDepth(self, record: TraceRecord) -> int:
        if record.kind == "spanBegin":
            depth = self._childDepthOf(record.containingSpanId)
            if record.spanId is not None:
                self._spanDepthBySpanId[record.spanId] = depth
            return depth
        if record.kind == "spanEnd":
            return (
                0
                if record.spanId is None
                else self._spanDepthBySpanId.get(record.spanId, 0)
            )
        return self._childDepthOf(record.containingSpanId)

    def _childDepthOf(self, containingSpanId: str | None) -> int:
        if containingSpanId is None:
            return 0
        parentDepth = self._spanDepthBySpanId.get(containingSpanId)
        return 0 if parentDepth is None else parentDepth + 1

    def _writeNamedValue(
        self,
        *,
        name: str,
        value: TraceValue | Mapping[str, TraceValue] | ExceptionSnapshot,
        depth: int,
        colorEnabled: bool,
        color: str,
    ) -> None:
        indentation = "  " * (depth + 1)
        continuationIndentation = "  " * (depth + 2)
        renderedValue = pprint.pformat(
            self._toDisplayValue(value),
            width=100,
            compact=True,
            sort_dicts=True,
        )
        renderedLines = renderedValue.splitlines() or [""]
        self._printLine(
            f"[ACTANT] {indentation}{name}={renderedLines[0]}",
            colorEnabled=colorEnabled,
            color=color,
        )
        for renderedLine in renderedLines[1:]:
            self._printLine(
                f"[ACTANT] {continuationIndentation}{renderedLine}",
                colorEnabled=colorEnabled,
                color=color,
            )

    def _toDisplayValue(
        self,
        value: TraceValue | Mapping[str, TraceValue] | ExceptionSnapshot,
    ) -> object:
        if isinstance(value, ExceptionSnapshot):
            renderedException: dict[str, object] = {
                "type": (
                    f"{value.typeModule}."
                    f"{value.typeQualifiedName}"
                ),
                "message": value.message,
            }
            if value.code is not None:
                renderedException["code"] = value.code
            if value.stack is not None:
                renderedException["stack"] = value.stack
            if value.attributes:
                renderedException["attributes"] = (
                    self._toDisplayValue(value.attributes)
                )
            return renderedException
        if isinstance(value, Mapping):
            return {key: self._toDisplayValue(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return tuple(self._toDisplayValue(item) for item in value)
        return value

    def _printLine(
        self,
        line: str,
        *,
        colorEnabled: bool,
        color: str,
    ) -> None:
        if colorEnabled:
            line = f"{color}{line}{_ANSI_RESET}"
        print(line, file=self._stream, flush=True)

    def _shouldWrite(self, level: TraceLevel) -> bool:
        return (
            _TRACE_LEVEL_RANKS[level]
            >= _TRACE_LEVEL_RANKS[self._minimumLevel]
        )

    def _isColorEnabled(self) -> bool:
        if self._colorMode == "never":
            return False
        if self._colorMode == "always":
            return True
        if "NO_COLOR" in os.environ:
            return False
        isatty = getattr(self._stream, "isatty", None)
        return bool(callable(isatty) and isatty())

    def _colorFor(self, record: TraceRecord) -> str:
        if record.level == "debug":
            return _ANSI_DIM
        if record.level == "info":
            return _ANSI_GREEN if record.kind == "spanEnd" else _ANSI_CYAN
        if record.level == "warning":
            return _ANSI_YELLOW
        if record.level == "error":
            return _ANSI_BRIGHT_RED
        return _ANSI_BOLD_RED

    def _symbolFor(self, record: TraceRecord) -> str:
        if record.kind == "spanBegin":
            return "▶"
        if record.kind == "spanEnd":
            if record.outcome == "failed":
                return "✘"
            if record.outcome == "cancelled":
                return "◼"
            return "■"
        return "•"

    def _formatTimestamp(self, timestampUnixNs: int) -> str:
        seconds = timestampUnixNs // 1_000_000_000
        milliseconds = (timestampUnixNs % 1_000_000_000) // 1_000_000
        localTime = time.localtime(seconds)
        return (
            f"{localTime.tm_hour:02d}:"
            f"{localTime.tm_min:02d}:"
            f"{localTime.tm_sec:02d}."
            f"{milliseconds:03d}"
        )


@dataclass(slots=True)
class _TraceBuilderBase:
    tracer: Tracer
    reason: str
    _level: TraceLevel = "info"
    _domain: str | None = None
    _message: str = ""
    _attributes: dict[str, object] = field(default_factory=dict)
    _exception: ExceptionSnapshot | None = None
    _causalParentEventIds: list[str] = field(default_factory=list)
    _parentMode: TraceParentMode = "ambient"
    _explicitParent: TraceParentContext | None = None
    _origin: str | None = None
    _consumed: bool = False

    def __post_init__(self) -> None:
        """Validate the builder's required reason."""
        _requireNonBlankString(self.reason, "reason")

    def level(self, level: TraceLevel) -> Self:
        self._requireNotConsumed()
        self._level = _requireTraceLevel(level)
        return self

    def domain(self, domain: str) -> Self:
        self._requireNotConsumed()
        self._domain = _requireNonBlankString(domain, "domain")
        return self

    def message(self, message: str) -> Self:
        self._requireNotConsumed()
        if not isinstance(message, str):
            raise TypeError(
                "message must be a string, "
                f"not {typeName(message)}.",
            )
        self._message = message
        return self

    def attr(self, key: str, value: TraceScalar) -> Self:
        self._requireNotConsumed()
        key = _requireNonBlankString(key, "attribute key")
        if not (value is None or isinstance(value, (bool, int, float, str))):
            raise TypeError(
                f"Scalar trace attribute {key!r} received {typeName(value)}. "
                "Use data() for structured trace data.",
            )
        self._attributes[key] = value
        return self

    def data(self, key: str, value: object) -> Self:
        self._requireNotConsumed()
        key = _requireNonBlankString(key, "attribute key")
        self._attributes[key] = value
        return self

    def parent(self, parent: TraceParentContext) -> Self:
        self._requireNotConsumed()
        self._requireUnselectedParentMode()
        self._explicitParent = self.tracer.requireParentContext(parent)
        self._parentMode = "explicit"
        return self

    def origin(self, origin: str) -> Self:
        self._requireNotConsumed()
        self._requireUnselectedParentMode()
        self._parentMode = "origin"
        self._origin = _requireNonBlankString(origin, "origin")
        return self

    def causedBy(self, *eventIds: str) -> Self:
        self._requireNotConsumed()
        for eventId in eventIds:
            clearEventId = _requireEventId(eventId)
            if clearEventId not in self._causalParentEventIds:
                self._causalParentEventIds.append(clearEventId)
        return self

    def attachException(
        self,
        exception: BaseException | ExceptionSnapshot,
        *,
        code: str | None = None,
        includeStack: bool | None = None,
    ) -> Self:
        self._requireNotConsumed()

        if self._exception is not None:
            raise RuntimeError(
                "Trace builder already contains exception evidence.",
            )

        if isinstance(exception, ExceptionSnapshot):
            if code is not None or includeStack is not None:
                raise ValueError(
                    "code and includeStack must not be "
                    "supplied with an ExceptionSnapshot.",
                )

            self._exception = exception
            return self

        if not isinstance(exception, BaseException):
            raise TypeError(
                "Exception must be a BaseException or ExceptionSnapshot, "
                f"not {typeName(exception)}.",
            )

        self._exception = self.tracer.captureException(
            exception,
            code=code,
            includeStack=includeStack,
        )

        return self

    def _consume(self) -> None:
        self._requireNotConsumed()
        self._consumed = True

    def _requireNotConsumed(self) -> None:
        if self._consumed:
            raise RuntimeError("Trace builder has already been consumed.")

    def _requireUnselectedParentMode(self) -> None:
        if self._parentMode != "ambient":
            raise RuntimeError(
                "Trace builder structural parent selection "
                "has already been configured.",
            )

    def _requireDomain(self) -> str:
        if self._domain is None:
            raise ValueError(
                "Trace builder requires domain() before "
                "emit() or start().",
            )
        return self._domain


@dataclass(slots=True)
class TraceEventBuilder(_TraceBuilderBase):
    def emit(self) -> str:
        domain = self._requireDomain()
        self._consume()
        return self.tracer._emitEvent(
            level=self._level,
            domain=domain,
            reason=self.reason,
            message=self._message,
            attributes=self._attributes,
            exception=self._exception,
            parentMode=self._parentMode,
            explicitParent=self._explicitParent,
            origin=self._origin,
            causalParentEventIds=tuple(self._causalParentEventIds),
        )


@dataclass(slots=True)
class TraceSpanBuilder(_TraceBuilderBase):
    endedReason: str | None = None
    completedReason: str | None = None
    failedReason: str | None = None
    cancelledReason: str | None = None
    completedLevel: TraceLevel | None = None
    failedLevel: TraceLevel | None = None
    cancelledLevel: TraceLevel | None = None

    def __post_init__(self) -> None:
        """Resolve a reason for every possible span outcome."""
        _TraceBuilderBase.__post_init__(self)

        endedReason = self._optionalReason(self.endedReason, "endedReason")
        self.endedReason = endedReason

        self.completedReason = self._resolveOutcomeReason(
            self.completedReason,
            endedReason,
            "completedReason",
        )
        self.completedLevel = self._optionalLevel(
            self.completedLevel,
            "completedLevel",
        )

        self.failedReason = self._resolveOutcomeReason(
            self.failedReason,
            endedReason,
            "failedReason",
        )
        self.failedLevel = self._optionalLevel(
            self.failedLevel,
            "failedLevel",
        )

        self.cancelledReason = self._resolveOutcomeReason(
            self.cancelledReason,
            endedReason,
            "cancelledReason",
        )
        self.cancelledLevel = self._optionalLevel(
            self.cancelledLevel,
            "cancelledLevel",
        )

    def _optionalLevel(
        self,
        value: TraceLevel | None,
        name: str,
    ) -> TraceLevel | None:
        if value is None:
            return None

        try:
            return _requireTraceLevel(value)
        except ValueError as err:
            raise ValueError(f"{name}: {err}") from err

    def _optionalReason(self, value: str | None, name: str) -> str | None:
        if value is None:
            return None
        return _requireNonBlankString(value, name)

    def _resolveOutcomeReason(
        self,
        specificReason: str | None,
        endedReason: str | None,
        name: str,
    ) -> str:
        if specificReason is not None:
            return _requireNonBlankString(specificReason, name)
        if endedReason is not None:
            return endedReason
        raise ValueError(f"{name} or endedReason must be provided.")

    def start(self) -> ActiveTraceSpan:
        domain = self._requireDomain()
        self._consume()

        if not isinstance(self.completedReason, str):
            raise TypeError(
                "completedReason must be a string, "
                f"not {typeName(self.completedReason)}.",
            )
        if not isinstance(self.failedReason, str):
            raise TypeError(
                "failedReason must be a string, "
                f"not {typeName(self.failedReason)}.",
            )
        if not isinstance(self.cancelledReason, str):
            raise TypeError(
                "cancelledReason must be a string, "
                f"not {typeName(self.cancelledReason)}.",
            )

        return self.tracer._startSpan(
            level=self._level,
            domain=domain,
            beginReason=self.reason,
            completedReason=self.completedReason,
            failedReason=self.failedReason,
            cancelledReason=self.cancelledReason,
            completedLevel=self.completedLevel,
            failedLevel=self.failedLevel,
            cancelledLevel=self.cancelledLevel,
            message=self._message,
            attributes=self._attributes,
            exception=self._exception,
            parentMode=self._parentMode,
            explicitParent=self._explicitParent,
            origin=self._origin,
            causalParentEventIds=tuple(self._causalParentEventIds),
        )


class ActiveTraceSpan:
    """
    Control one span lifecycle without being historical trace evidence.

    Span outcome, domain reason, severity, and captured exception evidence are
    independent. The span level is the fallback for every outcome; optional
    outcome-specific levels and manual-call overrides are explicit policy.

    Future managed-end staging may allow end-specific messages, attributes, and
    causal links without transferring lifecycle ownership away from the context
    manager.

    A span used as a context manager is owned exclusively by that context
    manager: complete(), fail() and cancel() are then forbidden. This prevents
    the record from claiming completion before the remainder of the managed
    block finishes.
    """

    def __init__(
        self,
        *,
        tracer: Tracer,
        spanId: str,
        beginEventId: str,
        structuralParent: TraceParentContext | None,
        domain: str,
        level: TraceLevel,
        completedReason: str,
        failedReason: str,
        cancelledReason: str,
        completedLevel: TraceLevel | None,
        failedLevel: TraceLevel | None,
        cancelledLevel: TraceLevel | None,
        startedMonotonicNs: int,
        parentLease: _TraceParentLease,
    ) -> None:
        self._tracer = tracer
        self._spanId = spanId
        self._beginEventId = beginEventId
        self._structuralParent = structuralParent
        self._domain = domain
        self._level = level
        self._completedReason = completedReason
        self._failedReason = failedReason
        self._cancelledReason = cancelledReason
        self._completedLevel = completedLevel
        self._failedLevel = failedLevel
        self._cancelledLevel = cancelledLevel
        self._startedMonotonicNs = startedMonotonicNs
        self._parentLease: _TraceParentLease | None = parentLease
        self._ended = False
        self._managed = False
        self._contextManagerExited = False

    @property
    def spanId(self) -> str:
        return self._spanId

    @property
    def beginEventId(self) -> str:
        return self._beginEventId

    @property
    def parent(self) -> TraceParentContext:
        return TraceParentContext(
            tracerId=self._tracer.tracerId,
            spanId=self._spanId,
            beginEventId=self._beginEventId,
        )

    def __enter__(self) -> Self:
        """Take exclusive context-manager ownership of this active span."""
        self._requireCurrentActive()
        if self._managed:
            raise RuntimeError(
                "Trace span has already entered a context manager.",
            )
        self._managed = True
        return self

    def __exit__(
        self,
        excType: type[BaseException] | None,
        exc: BaseException | None,
        tracebackObject: TracebackType | None,
    ) -> bool:
        """Finalize the managed span while always propagating its exception."""
        if not self._managed:
            raise RuntimeError(
                "Trace span did not enter this context manager.",
            )
        if self._contextManagerExited:
            raise RuntimeError(
                "Trace span context manager has already exited.",
            )
        self._contextManagerExited = True

        if exc is None:
            try:
                self._endManaged(
                    level=self._resolveOutcomeLevel(
                        "completed",
                        override=None,
                    ),
                    outcome="completed",
                    reason=self._completedReason,
                    exception=None,
                )
            except BaseException as finalizationError:
                self._recoverManagedFinalization(finalizationError)
                raise

            return False

        outcome: TraceSpanOutcome
        reason: str

        if isinstance(exc, asyncio.CancelledError):
            outcome = "cancelled"
            reason = self._cancelledReason
        else:
            outcome = "failed"
            reason = self._failedReason

        try:
            snapshot = self._tracer.captureException(exc)
            self._endManaged(
                level=self._resolveOutcomeLevel(
                    outcome,
                    override=None,
                ),
                outcome=outcome,
                reason=reason,
                exception=snapshot,
            )
        except BaseException as finalizationError:  # noqa: BLE001
            self._recoverManagedFinalization(finalizationError)

        return False

    def event(self, reason: str) -> TraceEventBuilder:
        self._requireCurrentActive()
        return self._tracer.event(reason).parent(self.parent)

    def span(
        self,
        beginReason: str,
        *,
        endedReason: str | None = None,
        completedReason: str | None = None,
        failedReason: str | None = None,
        cancelledReason: str | None = None,
        completedLevel: TraceLevel | None = None,
        failedLevel: TraceLevel | None = None,
        cancelledLevel: TraceLevel | None = None,
    ) -> TraceSpanBuilder:
        self._requireCurrentActive()
        return self._tracer.span(
            beginReason,
            endedReason=endedReason,
            completedReason=completedReason,
            failedReason=failedReason,
            cancelledReason=cancelledReason,
            completedLevel=completedLevel,
            failedLevel=failedLevel,
            cancelledLevel=cancelledLevel,
        ).parent(self.parent)

    def complete(
        self,
        *,
        message: str = "",
        attributes: Mapping[str, object] | None = None,
        causedBy: Sequence[str] = (),
        level: TraceLevel | None = None,
    ) -> str:
        self._requireManualEndAllowed()
        return self._end(
            level=self._resolveOutcomeLevel(
                "completed",
                override=level,
            ),
            outcome="completed",
            reason=self._completedReason,
            message=message,
            attributes=attributes,
            exception=None,
            causedBy=causedBy,
        )

    def fail(
        self,
        err: BaseException,
        *,
        reason: str | None = None,
        message: str = "",
        code: str | None = None,
        includeStack: bool | None = None,
        attributes: Mapping[str, object] | None = None,
        causedBy: Sequence[str] = (),
        level: TraceLevel | None = None,
    ) -> str:
        self._requireManualEndAllowed()
        self._requireCurrentActive()
        if not isinstance(err, BaseException):
            raise TypeError(f"err must be an exception, not {typeName(err)}.")
        snapshot = self._tracer.captureException(
            err,
            code=code,
            includeStack=includeStack,
        )
        return self._end(
            level=self._resolveOutcomeLevel(
                "failed",
                override=level,
            ),
            outcome="failed",
            reason=self._failedReason if reason is None else reason,
            message=message,
            attributes=attributes,
            exception=snapshot,
            causedBy=causedBy,
        )

    def cancel(
        self,
        *,
        reason: str | None = None,
        message: str = "",
        attributes: Mapping[str, object] | None = None,
        causedBy: Sequence[str] = (),
        level: TraceLevel | None = None,
    ) -> str:
        self._requireManualEndAllowed()
        return self._end(
            level=self._resolveOutcomeLevel(
                "cancelled",
                override=level,
            ),
            outcome="cancelled",
            reason=self._cancelledReason if reason is None else reason,
            message=message,
            attributes=attributes,
            exception=None,
            causedBy=causedBy,
        )

    def _resolveOutcomeLevel(
        self,
        outcome: TraceSpanOutcome,
        *,
        override: TraceLevel | None,
    ) -> TraceLevel:
        if override is not None:
            return _requireTraceLevel(override)

        configuredLevel: TraceLevel | None

        if outcome == "completed":
            configuredLevel = self._completedLevel
        elif outcome == "failed":
            configuredLevel = self._failedLevel
        else:
            configuredLevel = self._cancelledLevel

        return configuredLevel if configuredLevel is not None else self._level

    def _endManaged(
        self,
        *,
        level: TraceLevel,
        outcome: TraceSpanOutcome,
        reason: str,
        exception: ExceptionSnapshot | None,
    ) -> str:
        return self._end(
            level=level,
            outcome=outcome,
            reason=reason,
            message="",
            attributes=None,
            exception=exception,
            causedBy=(),
            managed=True,
        )

    def _end(
        self,
        *,
        level: TraceLevel,
        outcome: TraceSpanOutcome,
        reason: str,
        message: str,
        attributes: Mapping[str, object] | None,
        exception: ExceptionSnapshot | None,
        causedBy: Sequence[str],
        managed: bool = False,
    ) -> str:
        self._requireCurrentActive()
        if self._managed and not managed:
            raise RuntimeError(
                "A context-managed trace span may only "
                "be ended by its context manager.",
            )
        if (
            not isinstance(causedBy, Sequence)
            or isinstance(causedBy, (str, bytes, bytearray))
        ):
            raise TypeError(
                "causedBy must be a non-string sequence of "
                f"event IDs, not {typeName(causedBy)}.",
            )
        causalParentEventIds = tuple(
            _requireEventId(eventId) for eventId in causedBy
        )
        record = self._tracer._recordFactory.createSpanEnd(
            spanId=self._spanId,
            spanBeginEventId=self._beginEventId,
            level=level,
            domain=self._domain,
            reason=reason,
            message=message,
            attributes=attributes,
            exception=exception,
            structuralParent=self._structuralParent,
            outcome=outcome,
            durationNs=time.monotonic_ns() - self._startedMonotonicNs,
            causalParentEventIds=causalParentEventIds,
        )
        self._restoreAndMarkEnded()
        self._tracer._publisher.publish(record)
        return record.eventId

    def _recoverManagedFinalization(
        self,
        finalizationError: BaseException,
    ) -> None:
        if not self._ended:
            try:
                self._restoreAndMarkEnded()
            except BaseException as restorationError:  # noqa: BLE001
                self._tracer._emergencyChannel.reportSpanFinalizationFailure(
                    spanId=self._spanId,
                    err=restorationError,
                )
        self._tracer._emergencyChannel.reportSpanFinalizationFailure(
            spanId=self._spanId,
            err=finalizationError,
        )

    def _restoreAndMarkEnded(self) -> None:
        parentLease = self._requireParentLease()
        parentLease.restore()
        self._parentLease = None
        self._ended = True

    def _requireManualEndAllowed(self) -> None:
        if self._managed:
            raise RuntimeError(
                "A context-managed trace span may not be ended manually.",
            )

    def _requireParentLease(self) -> _TraceParentLease:
        parentLease = self._parentLease
        if parentLease is None:
            raise RuntimeError(
                "Trace span no longer owns an ambient parent lease.",
            )
        return parentLease

    def _requireCurrentActive(self) -> None:
        if self._ended:
            raise RuntimeError("Trace span has already ended.")

        self._requireParentLease().requireOwner()
        currentParent = self._tracer._runtimeContext.currentParent()

        if (
            currentParent is None
            or currentParent.tracerId != self._tracer.tracerId
            or currentParent.spanId != self._spanId
            or currentParent.beginEventId != self._beginEventId
        ):
            raise RuntimeError(
                "Trace span is not the active span "
                "in the current execution context.",
            )


class Tracer:
    """Provide the fluent tracing API and coordinate tracing components."""

    def __init__(
        self,
        *,
        destinations: Sequence[TraceDestination],
        recordFactory: TraceRecordFactory | None = None,
        runtimeContext: TraceRuntimeContext | None = None,
        emergencyReporter: TraceEmergencyReporter | None = None,
    ) -> None:
        tracerId = _newTracerId()
        resolvedRuntimeContext = (
            runtimeContext
            if runtimeContext is not None
            else TraceRuntimeContext()
        )
        resolvedRecordFactory = (
            recordFactory
            if recordFactory is not None
            else TraceRecordFactory()
        )
        resolvedEmergencyReporter = (
            emergencyReporter
            if emergencyReporter is not None
            else TraceEmergencyReporter()
        )
        emergencyChannel = TraceEmergencyChannel(
            reporter=resolvedEmergencyReporter,
        )
        publisher = TracePublisher(
            destinations=destinations,
            runtimeContext=resolvedRuntimeContext,
            emergencyChannel=emergencyChannel,
        )

        resolvedRuntimeContext.bindTracer(tracerId)

        self._tracerId = tracerId
        self._runtimeContext = resolvedRuntimeContext
        self._recordFactory = resolvedRecordFactory
        self._emergencyChannel = emergencyChannel
        self._publisher = publisher
        self._context = TraceContext(
            tracerId=tracerId,
            runtimeContext=resolvedRuntimeContext,
        )

    @property
    def tracerId(self) -> str:
        return self._tracerId

    @property
    def context(self) -> TraceContext:
        """Return the read-only transferable trace-context facade."""
        return self._context

    def event(self, reason: str) -> TraceEventBuilder:
        return TraceEventBuilder(
            tracer=self,
            reason=_requireNonBlankString(reason, "reason"),
        )

    def span(
        self,
        beginReason: str,
        *,
        endedReason: str | None = None,
        completedReason: str | None = None,
        failedReason: str | None = None,
        cancelledReason: str | None = None,
        completedLevel: TraceLevel | None = None,
        failedLevel: TraceLevel | None = None,
        cancelledLevel: TraceLevel | None = None,
    ) -> TraceSpanBuilder:
        return TraceSpanBuilder(
            tracer=self,
            reason=_requireNonBlankString(beginReason, "beginReason"),
            endedReason=endedReason,
            completedReason=completedReason,
            failedReason=failedReason,
            cancelledReason=cancelledReason,
            completedLevel=completedLevel,
            failedLevel=failedLevel,
            cancelledLevel=cancelledLevel,
        )

    def requireParentContext(
        self,
        parent: TraceParentContext,
    ) -> TraceParentContext:
        if not isinstance(parent, TraceParentContext):
            raise TypeError(f"parent must be a TraceParentContext, not {typeName(parent)}.")
        if parent.tracerId != self._tracerId:
            raise ValueError("Trace parent belongs to a different tracer.")
        return parent

    def captureException(
        self,
        err: BaseException,
        *,
        code: str | None = None,
        includeStack: bool | None = None,
    ) -> ExceptionSnapshot:
        return (
            self._recordFactory.captureException(
                err,
                code=code,
                includeStack=includeStack,
            )
        )

    def _emitEvent(
        self,
        *,
        level: TraceLevel,
        domain: str,
        reason: str,
        message: str,
        attributes: Mapping[str, object] | None,
        exception: ExceptionSnapshot | None,
        parentMode: TraceParentMode,
        explicitParent: TraceParentContext | None,
        origin: str | None,
        causalParentEventIds: tuple[str, ...],
    ) -> str:
        self._runtimeContext.requireNormalTracingAllowed()
        structuralParent, resolvedOrigin = self._resolveStructuralParent(
            parentMode=parentMode,
            explicitParent=explicitParent,
            origin=origin,
        )
        record = self._recordFactory.createEvent(
            level=level,
            domain=domain,
            reason=reason,
            message=message,
            attributes=attributes,
            exception=exception,
            structuralParent=structuralParent,
            origin=resolvedOrigin,
            causalParentEventIds=causalParentEventIds,
        )
        self._publisher.publish(record)
        return record.eventId

    def _startSpan(
        self,
        *,
        level: TraceLevel,
        domain: str,
        beginReason: str,
        completedReason: str,
        failedReason: str,
        cancelledReason: str,
        completedLevel: TraceLevel | None,
        failedLevel: TraceLevel | None,
        cancelledLevel: TraceLevel | None,
        message: str,
        attributes: Mapping[str, object] | None,
        exception: ExceptionSnapshot | None,
        parentMode: TraceParentMode,
        explicitParent: TraceParentContext | None,
        origin: str | None,
        causalParentEventIds: tuple[str, ...],
    ) -> ActiveTraceSpan:
        self._runtimeContext.requireNormalTracingAllowed()
        structuralParent, resolvedOrigin = self._resolveStructuralParent(
            parentMode=parentMode,
            explicitParent=explicitParent,
            origin=origin,
        )
        spanId = self._recordFactory.newSpanId()
        startedMonotonicNs = time.monotonic_ns()
        beginRecord = self._recordFactory.createSpanBegin(
            spanId=spanId,
            level=level,
            domain=domain,
            reason=beginReason,
            message=message,
            attributes=attributes,
            exception=exception,
            structuralParent=structuralParent,
            origin=resolvedOrigin,
            causalParentEventIds=causalParentEventIds,
        )
        parentContext = TraceParentContext(
            tracerId=self._tracerId,
            spanId=spanId,
            beginEventId=beginRecord.eventId,
        )
        parentLease = self._runtimeContext.installParent(parentContext)
        try:
            self._publisher.publish(beginRecord)
            return ActiveTraceSpan(
                tracer=self,
                spanId=spanId,
                beginEventId=beginRecord.eventId,
                structuralParent=structuralParent,
                domain=domain,
                level=level,
                completedReason=completedReason,
                failedReason=failedReason,
                cancelledReason=cancelledReason,
                completedLevel=completedLevel,
                failedLevel=failedLevel,
                cancelledLevel=cancelledLevel,
                startedMonotonicNs=startedMonotonicNs,
                parentLease=parentLease,
            )
        except BaseException:
            try:
                parentLease.restore()
            except BaseException as restorationError:  # noqa: BLE001
                self._emergencyChannel.reportSpanFinalizationFailure(
                    spanId=spanId,
                    err=restorationError,
                )
            raise

    def _resolveStructuralParent(
        self,
        *,
        parentMode: TraceParentMode,
        explicitParent: TraceParentContext | None,
        origin: str | None,
    ) -> tuple[TraceParentContext | None, str | None]:
        if parentMode == "explicit":
            if explicitParent is None:
                raise RuntimeError(
                    "Explicit trace parent mode requires a parent context.",
                )
            if origin is not None:
                raise RuntimeError(
                    "Explicit trace parent mode must not contain origin.",
                )
            return self.requireParentContext(explicitParent), None

        if parentMode == "origin":
            if explicitParent is not None:
                raise RuntimeError(
                    "Trace origin mode must not contain a parent context.",
                )
            if origin is None:
                raise RuntimeError("Trace origin mode requires an origin.")
            return None, _requireNonBlankString(origin, "origin")

        if parentMode != "ambient":
            raise ValueError(f"Unsupported trace parent mode {parentMode!r}.")

        if explicitParent is not None:
            raise RuntimeError(
                "Ambient trace parent mode must not "
                "contain an explicit parent context.",
            )

        if origin is not None:
            raise RuntimeError(
                "Ambient trace parent mode must not contain origin.",
            )

        ambientParent = self._runtimeContext.currentParent()
        if ambientParent is None:
            raise RuntimeError(
                "Trace record has no ambient structural parent. Select an "
                "explicit parent with parent() or declare a structural "
                "parent with origin().",
            )
        return self.requireParentContext(ambientParent), None


def createDefaultTracer() -> Tracer:
    """
    Creates the current development tracer.

    Trace output is intentionally written to stdout and may be noisy.
    """

    return Tracer(
        destinations=(
            ConsoleTraceDestination(
                stream=sys.stdout,
                colorMode="auto",
                minimumLevel="debug",
                showTimestamp=True,
                showAttributes=True,
            ),
        ),
        recordFactory=TraceRecordFactory(
            includeExceptionStacks=True,
        ),
    )
