# backend/core/tracing.py
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import inspect
import threading
import time
import traceback
from asyncio import AbstractEventLoop
from collections import deque
from collections.abc import Coroutine, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, cast, Literal, Self

from backend.core.ids import uuidv7
from backend.core.jsonutils import tryJSONify

JsonDict = dict[str, Any]

_DEFAULT_JSONIFY_MAX_DEPTH = 50
_EMPTY_MAP: Mapping[str, Any] = MappingProxyType({})

CorrelationScope = Literal["span", "event", "any"]


def _utcNowUnixMs() -> int:
    return time.time_ns() // 1_000_000


def _isScalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _normalizeLevel(level: str | None) -> str:
    # DX-friendly defaults, but allow any string.
    if not isinstance(level, str) or not level:
        return "info"
    levelLower = level.lower()
    if levelLower in ("debug", "info", "warning", "error"):
        return levelLower
    return level


class _UnsetType:
    __slots__ = ()
    
    def __repr__(self) -> str:
        return "_UNSET"

_UNSET = _UnsetType()


# -----------------------------------------------------------------------------
# Context propagation
# -----------------------------------------------------------------------------
# Python contextvars propagate into asyncio.create_task() by default (Py3.11+).
# We use them as the *ambient* tracing state for the current Task:
# - current span (structural nesting)
# - current parentRecordId (causal DAG parent)
# - current traceGraphId (graph/session/run identity)
# - current correlation context (scalar-only allowlist)
_spanVar: contextvars.ContextVar["TraceSpan | None"] = contextvars.ContextVar(
    "turnix_trace_current_span",
    default=None,
)
_parentRecordIdVar: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "turnix_trace_parent_record_id",
    default=None,
)
_traceGraphIdVar: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "turnix_trace_graph_id",
    default=None,
)
_correlationVar: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "turnix_trace_correlation",
    default=None,
)

# Internal guard against recursive "tracer emits warning about tracer" loops.
_internalEmitDepthVar: contextvars.ContextVar[int] = contextvars.ContextVar(
    "turnix_trace_internal_emit_depth",
    default=0,
)


# -----------------------------------------------------------------------------
# Registry (kernel-tier semantic dictionary)
# -----------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CorrelationKeySpec:
    key: str
    promoteTopLevel: bool = True
    index: bool = True
    scopeDefault: CorrelationScope = "span"


@dataclass(frozen=True, slots=True)
class EventSpec:
    name: str
    domain: str
    defaultLevel: str = "debug"
    requiredReason: bool = False


class TraceRegistry:
    """
    Kernel-tier trace vocabulary registry.
    
    Minimal now, but future-proof.
    - correlation keys allowlist + promotion/index hints + scope-default policy
    - optional event specs (domain + defaults + policy)
    """

    def __init__(self) -> None:
        self._corr: dict[str, CorrelationKeySpec] = {}
        self._events: dict[str, EventSpec] = {}
        self._lock = threading.RLock()
    
    # ----- Correlation keys -----
    def registerCorrelationKey(
        self,
        key: str,
        *,
        promoteTopLevel: bool = True,
        index: bool = True,
        scopeDefault: CorrelationScope = "span",
    ) -> None:
        if not isinstance(key, str) or not key:
            raise ValueError("Correlation key must be a non-empty string")
        if scopeDefault not in ("span", "event", "any"):
            raise ValueError("scopeDefault must be 'span', 'event' or 'any'")
        with self._lock:
            self._corr[key] = CorrelationKeySpec(
                key=key,
                promoteTopLevel=promoteTopLevel,
                index=index,
                scopeDefault=scopeDefault,
            )

    def isAllowedCorrelationKey(self, key: str) -> bool:
        with self._lock:
            return key in self._corr
    
    def getCorrelationKeySpec(self, key: str) -> CorrelationKeySpec | None:
        with self._lock:
            return self._corr.get(key)
    
    def listCorrelationKeys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._corr.keys())

    # ----- Event specs -----
    def registerEvent(
        self,
        name: str,
        *,
        domain: str,
        defaultLevel: str = "debug",
        requiredReason: bool = False,
    ) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("Event name must be a non-empty string")
        if not isinstance(domain, str) or not domain:
            raise ValueError("Event domain must be a non-empty string")
        spec = EventSpec(
            name=name,
            domain=domain,
            defaultLevel=_normalizeLevel(defaultLevel),
            requiredReason=requiredReason,
        )
        with self._lock:
            self._events[name] = spec

    def getEventSpec(self, name: str) -> EventSpec | None:
        with self._lock:
            return self._events.get(name)


# -----------------------------------------------------------------------------
# TraceHub (buffer + subscribers)
# -----------------------------------------------------------------------------
def _queuePutNowait(queue: asyncio.Queue[JsonDict], record: JsonDict) -> None:
    try:
        queue.put_nowait(record)
    except asyncio.QueueFull:
        pass


class TraceHub:
    """
    In-memory ring buffer + live subscribers.
    
    emit(record):
      - appends to buffer
      - fans out to subscribers via loop.call_soon_threadsafe()
    
    subscribe():
      - returns (snapshot, queue)
    
    unsubscribe(queue)
      - detach subscriber
    
    Tracing must not block.
    """

    def __init__(self, capacity: int = 5000) -> None:
        self.capacity = max(1, int(capacity))
        self._buffer: deque[JsonDict] = deque(maxlen=self.capacity)
        self._subscribers: dict[asyncio.Queue[JsonDict], AbstractEventLoop] = {}
        self._lock = threading.Lock()
    
    def emit(self, record: JsonDict) -> None:
        # Shallow-freeze record + key containers to reduce accidental mutation leaks.
        try:
            frozen = dict(record)
            tags = frozen.get("tags")
            if isinstance(tags, list):
                frozen["tags"] = list(tags)
            corr = frozen.get("correlation")
            if isinstance(corr, dict):
                frozen["correlation"] = dict(corr)
            attrs = frozen.get("attrs")
            if isinstance(attrs, dict):
                frozen["attrs"] = dict(attrs)
            err = frozen.get("error")
            if isinstance(err, dict):
                frozen["error"] = dict(err)
            src = frozen.get("source")
            if isinstance(src, dict):
                frozen["source"] = dict(src)
        except Exception:
            frozen = record
        
        with self._lock:
            self._buffer.append(frozen)
            subscribers = list(self._subscribers.items())
        
        for queue, loop in subscribers:
            try:
                loop.call_soon_threadsafe(_queuePutNowait, queue, frozen)
            except RuntimeError:
                with self._lock:
                    self._subscribers.pop(queue, None)

    def subscribe(self) -> tuple[list[JsonDict], asyncio.Queue[JsonDict]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[JsonDict] = asyncio.Queue(maxsize=1000)
        with self._lock:
            snapshot = list(self._buffer)
            self._subscribers[queue] = loop
        return snapshot, queue
    
    def unsubscribe(self, queue: asyncio.Queue[JsonDict]) -> None:
        with self._lock:
            self._subscribers.pop(queue, None)


# -----------------------------------------------------------------------------
# Core value objects
# -----------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TraceSpan:
    # Structural identity
    traceGraphId: str
    spanId: str
    spanName: str
    parentSpanId: str | None
    
    # Record ids anchoring this span in the trace DAG (start and end have their own ids)
    spanStartRecordId: str
    
    # Correlation snapshot at span creation (scalar-only allowlist, read-only)
    correlation: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAP)

    startUnixMs: int = field(default_factory=_utcNowUnixMs)


@dataclass(frozen=True, slots=True)
class TraceError:
    code: str | None = None
    type: str | None = None
    message: str | None = None
    stack: str | None = None
    data: Any = None
    
    def toJson(self) -> JsonDict:
        out: JsonDict = {}
        if self.code is not None:
            out["code"] = self.code
        if self.type is not None:
            out["type"] = self.type
        if self.message is not None:
            out["message"] = self.message
        if self.stack is not None:
            out["stack"] = self.stack
        if self.data is not None:
            try:
                out["data"] = tryJSONify(self.data, _maxDepth=_DEFAULT_JSONIFY_MAX_DEPTH)
            except Exception:
                out["data"] = {"__repr__": repr(self.data)}
        return out


@dataclass(frozen=True, slots=True)
class ResolvedRecordContext:
    traceGraphId: str
    span: TraceSpan | None
    parentRecordId: str | None
    correlation: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ResolvedSpanStartContext:
    traceGraphId: str
    parentSpan: TraceSpan | None
    parentRecordId: str | None
    correlation: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class TraceOkOutcome:
    kind: Literal["ok"] = "ok"
    level: str = "info"
    reason: str = "Completed"
    message: str | None = None
    attrs: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAP)


@dataclass(frozen=True, slots=True)
class TraceErrorOutcome:
    kind: Literal["error"] = "error"
    level: str = "error"
    reason: str = "Failed"
    message: str | None = None
    error: TraceError | None = None
    attrs: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAP)


@dataclass(frozen=True, slots=True)
class TraceCancelledOutcome:
    kind: Literal["cancelled"] = "cancelled"
    level: str = "info"
    reason: str = "Cancelled"
    message: str | None = None
    cancelCategory: str | None = None
    attrs: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAP)


@dataclass(frozen=True, slots=True)
class TraceTimeoutOutcome:
    kind: Literal["timeout"] = "timeout"
    level: str = "warning"
    reason: str = "Timeout"
    message: str | None = None
    timeoutMs: int | None = None
    attrs: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAP)


@dataclass(frozen=True, slots=True)
class TraceDebuggerIntervenedOutcome:
    kind: Literal["debuggerIntervened"] = "debuggerIntervened"
    level: str = "warning"
    reason: str = "DebuggerIntervened"
    message: str | None = None
    debuggerAction: str | None = None
    debuggerClientId: str | None = None
    requestId: str | None = None
    attrs: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAP)


@dataclass(frozen=True, slots=True)
class TracePolicyRefusedOutcome:
    kind: Literal["policyRefused"] = "policyRefused"
    level: str = "warning"
    reason: str = "PolicyRefused"
    message: str | None = None
    policyId: str | None = None
    refusalCategory: str | None = None
    attrs: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAP)


TraceOutcome = (
    TraceOkOutcome
    | TraceErrorOutcome
    | TraceCancelledOutcome
    | TraceTimeoutOutcome
    | TraceDebuggerIntervenedOutcome
    | TracePolicyRefusedOutcome
)


@dataclass(frozen=True, slots=True)
class ResolvedSpanEndOutcome:
    status: str
    level: str
    reason: str
    message: str | None
    attrs: Mapping[str, Any]
    error: TraceError | None


@dataclass(slots=True)
class SpanScope:
    """
    Owns the contextvars tokens for a span, so unwinding is correct.
    
    Robustness rules:
    - Ending from a different Task is treated as misuse:
      • we do NOT end the span
      • we do NOT attempt to reset tokens (would be invalid)
      • we emit a warning event explaining the misuse
    """
    
    tracer: "Tracer"
    span: TraceSpan
    
    _spanToken: contextvars.Token["TraceSpan | None"]
    _parentRecordIdToken: contextvars.Token[str | None]
    _traceGraphIdToken: contextvars.Token[str | None]
    _correlationToken: contextvars.Token[dict[str, Any] | None]
    _task: asyncio.Task[Any] | None
    
    _ended: bool = False
    
    async def __aenter__(self) -> "SpanScope":
        return self
    
    async def __aexit__(self, _excType, exc, _tb) -> None:
        if exc is None:
            self.ok()
        else:
            self.fail(
                reason="ExceptionRaised",
                message=str(exc),
                error=self.tracer.errorFromException(exc),
            )
    
    def ok(
        self,
        *,
        reason: str = "Completed",
        message: str | None = None,
        attrs: Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        level: str = "info"
    ) -> None:
        self.end(
            TraceOkOutcome(
                level=level,
                reason=reason,
                message=message,
                attrs=attrs or _EMPTY_MAP,
            ),
            tags=tags,
            domain=domain,
        )
    
    def fail(
        self,
        *,
        reason: str = "Failed",
        message: str | None = None,
        error: TraceError | None = None,
        attrs: Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        level: str = "error",
    ) -> None:
        self.end(
            TraceErrorOutcome(
                level=level,
                reason=reason,
                message=message,
                error=error,
                attrs=attrs or _EMPTY_MAP,
            ),
            tags=tags,
            domain=domain,
        )
    
    def error(
        self,
        *,
        reason: str = "Failed",
        message: str | None = None,
        error: TraceError | None = None,
        attrs: Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        level: str = "error",
    ) -> None:
        """Alias for fail(). See fail()."""
        self.fail(
            reason=reason,
            message=message,
            error=error,
            attrs=attrs,
            tags=tags,
            domain=domain,
            level=level,
        )
    
    def cancel(
        self,
        *,
        reason: str = "Cancelled",
        message: str | None = None,
        cancelCategory: str | None = None,
        attrs: Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        level: str = "info",
    ) -> None:
        self.end(
            TraceCancelledOutcome(
                level=level,
                reason=reason,
                message=message,
                cancelCategory=cancelCategory,
                attrs=attrs or _EMPTY_MAP,
            ),
            tags=tags,
            domain=domain,
        )

    def timeout(
        self,
        *,
        reason: str = "TimedOut",
        message: str | None = None,
        timeoutMs: int | None = None,
        attrs: Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        level: str = "warning",
    ) -> None:
        self.end(
            TraceTimeoutOutcome(
                level=level,
                reason=reason,
                message=message,
                timeoutMs=timeoutMs,
                attrs=attrs or _EMPTY_MAP,
            ),
            tags=tags,
            domain=domain,
        )
    
    def policyRefused(
        self,
        *,
        reason: str = "PolicyRefused",
        message: str | None = None,
        policyId: str | None = None,
        refusalCategory: str | None = None,
        attrs: Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        level: str = "warning",
    ) -> None:
        self.end(
            TracePolicyRefusedOutcome(
                level=level,
                reason=reason,
                message=message,
                policyId=policyId,
                refusalCategory=refusalCategory,
                attrs=attrs or _EMPTY_MAP,
            ),
            tags=tags,
            domain=domain,
        )

    def debuggerIntervened(
        self,
        *,
        reason: str = "DebuggerIntervened",
        message: str | None = None,
        debuggerAction: str | None = None,
        debuggerClientId: str | None = None,
        requestId: str | None = None,
        attrs: Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        level: str = "warning",
    ) -> None:
        self.end(
            TraceDebuggerIntervenedOutcome(
                level=level,
                reason=reason,
                message=message,
                debuggerAction=debuggerAction,
                debuggerClientId=debuggerClientId,
                requestId=requestId,
                attrs=attrs or _EMPTY_MAP,
                ),
            tags=tags,
            domain=domain,
        )
    
    def end(
        self,
        outcome: TraceOutcome | None = None,
        *,
        tags: list[str] | None = None,
        domain: str | None = None,
    ) -> None:
        if self._ended:
            return
        
        if outcome is None:
            outcome = TraceOkOutcome()
        
        currentTask = self.tracer._safeCurrentTask()
        if self._task is not None and currentTask is not None and currentTask is not self._task:
            # Cross-task end attempt: do not reset tokens (invalid). Emit evidence only.
            # Important: do NOT put these in correlation (they'd propagate). Keep as attrs.
            self.tracer.event("trace.span.crossTaskAttemptedEnd") \
                .level("warning") \
                .domain("trace") \
                .reason("CrossTaskAttemptedSpanEnd") \
                .message("SpanScope.end() was attempted from a different asyncio Task; owner task must end the span") \
                .attr("taskCreatedId", id(self._task)) \
                .attr("taskAttemptedId", id(currentTask)) \
                .attr("requestedOutcomeKind", getattr(outcome, "kind", None)) \
                .span(self.span) \
                .emit()
            
            return
        
        self._ended = True
        
        # Normal unwind: reset tokens in the same context that created them.
        try:
            try:
                _parentRecordIdVar.reset(self._parentRecordIdToken)
                _correlationVar.reset(self._correlationToken)
            finally:
                _traceGraphIdVar.reset(self._traceGraphIdToken)
        finally:
            _spanVar.reset(self._spanToken)
        
        self.tracer._emitSpanEnd(
            self.span,
            tags=tags,
            outcome=outcome,
            domain=domain,
        )


# -----------------------------------------------------------------------------
# Fluent builders (composition; mutable but short lived)
# -----------------------------------------------------------------------------
@dataclass(slots=True)
class _RecordCommonBuilder:
    tracer: "Tracer"
    _name: str
    
    _level: str | None = None
    _tags: list[str] = field(default_factory=list)
    _domain: str | None = None
    _reason: str | None = None
    _message: str | None = None
    
    _attrs: JsonDict = field(default_factory=dict)
    
    _spanCorrelation: JsonDict = field(default_factory=dict)
    _eventCorrelation: JsonDict = field(default_factory=dict)
    
    _parentRecordId: str | None | _UnsetType = _UNSET
    _span: TraceSpan | None | _UnsetType = _UNSET
    
    _captureSource: bool = False
    
    def level(self, level: str) -> Self:
        self._level = _normalizeLevel(level)
        return self
    
    def tags(self, *tags: str) -> Self:
        for tag in tags:
            if isinstance(tag, str) and tag:
                self._tags.append(tag)
        return self
    
    def domain(self, domain: str) -> Self:
        if not isinstance(domain, str) or not domain:
            raise ValueError("domain must be a non-empty string")
        self._domain = domain
        return self

    def reason(self, reason: str) -> Self:
        if not isinstance(reason, str) or not reason:
            raise ValueError("reason must be a non-empty string")
        self._reason = reason
        return self
    
    def message(self, message: str) -> Self:
        if not isinstance(message, str):
            raise TypeError("message must be a string")
        self._message = message
        return self
    
    def parent(self, parentRecordId: str | None) -> Self:
        if parentRecordId is not None and (not isinstance(parentRecordId, str) or not parentRecordId):
            raise TypeError("parentRecordId must be a non-empty string")
        self._parentRecordId = parentRecordId
        return self
    
    def span(self, span: TraceSpan | None) -> Self:
        self._span = span
        return self
    
    def corrSpan(self, **values: Any) -> Self:
        for key, value in values.items():
            self.tracer._validateCorrelationKV(key, value)
            self._spanCorrelation[key] = value
        return self
    
    def corrEvent(self, **values: Any) -> Self:
        for key, value in values.items():
            self.tracer._validateCorrelationKV(key, value)
            self._eventCorrelation[key] = value
        return self
    
    def corr(self, **values: Any) -> Self:
        """
        Convenience routing:
        - uses registry scopeDefault to route keys to span or event correlation
        - 'any' routes to event correlation (record-local by default)
        """
        for key, value in values.items():
            self.tracer._validateCorrelationKV(key, value)
            spec = self.tracer.registry.getCorrelationKeySpec(key)
            scope = spec.scopeDefault if spec is not None else "any"
            if scope == "span":
                self._spanCorrelation[key] = value
            else:
                self._eventCorrelation[key] = value
        return self
    
    def attr(self, key: str, value: Any) -> Self:
        if not isinstance(key, str) or not key:
            raise ValueError("attr key must be a non-empty string")
        if not _isScalar(value):
            raise TypeError(f"attr '{key}' must be a scalar, got {type(value).__name__}")
        self._attrs[key] = value
        return self
    
    def attrJson(self, key: str, value: Any) -> Self:
        if not isinstance(key, str) or not key:
            raise ValueError("attrJson key must be a non-empty string")
        try:
            self._attrs[key] = tryJSONify(value, _maxDepth=_DEFAULT_JSONIFY_MAX_DEPTH)
        except Exception as err:
            self._attrs[key] = {
                "__jsonifyError__": True,
                "type": type(err).__name__,
                "message": str(err),
            }
        return self
    
    def captureSource(self, enabled: bool = True) -> Self:
        self._captureSource = bool(enabled)
        return self


@dataclass(slots=True)
class _SpanBuilder(_RecordCommonBuilder):
    def start(self) -> SpanScope:
        return self.tracer.startSpan(
            self._name,
            level=self._level,
            tags=self._tags,
            domain=self._domain,
            reason=self._reason,
            message=self._message,
            attrs=self._attrs or None,
            spanCorrelation=self._spanCorrelation or None,
            eventCorrelation=self._eventCorrelation or None,
            parentRecordId=self._parentRecordId,
            captureSource=self._captureSource,
        )


@dataclass(slots=True)
class _EventBuilder(_RecordCommonBuilder):
    _error: TraceError | None = None
    
    def error(
        self,
        *,
        code: str | None = None,
        type: str | None = None,
        message: str | None = None,
        stack: str | None = None,
        data: Any = None,
    ) -> Self:
        self._error = TraceError(code=code, type=type, message=message, stack=stack, data=data)
        return self
    
    def emit(self) -> str:
        return self.tracer.traceEvent(
            self._name,
            level=self._level,
            tags=self._tags,
            domain=self._domain,
            reason=self._reason,
            message=self._message,
            attrs=self._attrs or None,
            eventCorrelation=self._eventCorrelation or None,
            parentRecordId=self._parentRecordId,
            span=self._span,
            error=self._error,
            captureSource=self._captureSource,
        )


# -----------------------------------------------------------------------------
# Tracer (kernel-tier)
# -----------------------------------------------------------------------------
class Tracer:
    """
    Turnix tracer.
    
    Design goals:
    - robust causal DAG linking via parentRecordId (explicit)
    - robust structural nesting via spans (spanId/parentSpanId)
    - hard-to-misuse context unwinding via SpanScope (tokens are not stored on TraceSpan)
    - future-proof vocabulary via TraceRegistry
    - clean separation: correlation (shared/allowlisted scalars) vs attrs (local payload)
    - task spawning with explicit context propagation and optional wrapper span
    
    Standard record fields:
      traceRecordId, traceGraphId, recordType, spanId, parentSpanId, parentRecordId,
      name, level, tags, domain, reason, message, correlation, attrs, error,
      timeUnixMs, seq, source (optional)
    
    Notes:
    - correlation keys must be registered (TraceRegistry)
    - correlation values must be scalar (None|bool|int|float|str)
    - attrs is arbitrary JSON-ish via attrJson (bounded) or scalar via attr()
    """
    
    def __init__(self, hub: TraceHub | None = None, registry: TraceRegistry | None = None) -> None:
        self.hub = hub or TraceHub()
        self.registry = registry or TraceRegistry()
        
        self._seq = 0
        self._seqLock = threading.Lock()
        self._kernelRootScope: SpanScope | None = None
        self._kernelRootLock = threading.RLock()

    # ----- Registry helpers -----
    
    def registerCorrelationKeys(self, keys: Iterable[str]) -> None:
        for key in keys:
            self.registry.registerCorrelationKey(key)
    
    def _validateCorrelationKV(self, key: str, value: Any) -> None:
        if not isinstance(key, str) or not key:
            raise ValueError("Correlation key must be a non-empty string")
        if not self.registry.isAllowedCorrelationKey(key):
            raise KeyError(f"Unknown correlation key '{key}'")
        if not _isScalar(value):
            raise TypeError(f"Correlation '{key}' must be a scalar, got {type(value).__name__}")

    def _collectCorrelationScopeMismatches(
        self,
        correlation: Mapping[str, Any],
        *,
        usedIn: CorrelationScope
    ) -> list[JsonDict]:
        mismatches: list[JsonDict] = []
        for key in correlation.keys():
            spec = self.registry.getCorrelationKeySpec(key)
            if spec is None:
                continue
            if spec.scopeDefault == "any":
                continue
            if spec.scopeDefault != usedIn:
                mismatches.append({
                    "key": key,
                    "expected": spec.scopeDefault,
                    "usedIn": usedIn,
                })
        return mismatches
    
    # ----- Fluent API -----
    
    def span(self, name: str) -> _SpanBuilder:
        return _SpanBuilder(self, _name=name)
    
    def event(self, name: str) -> _EventBuilder:
        return _EventBuilder(self, _name=name)

    # ----- Ambient context -----
    
    def _nextSeq(self) -> int:
        with self._seqLock:
            self._seq += 1
            return self._seq
    
    def _currentSpan(self) -> TraceSpan | None:
        return _spanVar.get(None)
    
    def _currentParentRecordId(self) -> str | None:
        return _parentRecordIdVar.get(None)
    
    def _currentTraceGraphId(self) -> str | None:
        return _traceGraphIdVar.get(None)
    
    def _safeCurrentTask(self) -> asyncio.Task[Any] | None:
        with contextlib.suppress(RuntimeError):
            return asyncio.current_task()
        return None
    
    def _isCurrentTaskOwner(self, scope: SpanScope) -> bool:
        currentTask = self._safeCurrentTask()
        if scope._task is None or currentTask is None:
            return True
        return currentTask is scope._task
    
    def _currentCorrelation(self) -> dict[str, Any]:
        corr = _correlationVar.get()
        return dict(corr) if isinstance(corr, dict) else {}
    
    def updateCorrelation(self, values: Mapping[str, Any]) -> None:
        """
        Manual ambient merge.
        Prefer spanCorrelation on startSpan boundaries.
        """
        current = self._currentCorrelation()
        for key, value in values.items():
            self._validateCorrelationKV(key, value)
            current[key] = value
        _correlationVar.set(current)
    
    def setTraceGraphId(self, traceGraphId: str) -> None:
        if not isinstance(traceGraphId, str) or not traceGraphId:
            raise ValueError("traceGraphId must be a non-empty string")
        _traceGraphIdVar.set(traceGraphId)
    
    def ensureTraceGraphId(self) -> str:
        """
        Single authority for the active traceGraphId.
        
        Rules:
        - If a span is active, its traceGraphId is authoritative; ambient mismatch is warned and repaired.
        - Else, if ambient traceGraphId exists, it is used.
        - Else, a new traceGraphId is created and persisted into ambient context.
        """
        currentSpan = self._currentSpan()
        if currentSpan is not None:
            currentTraceGraphId = self._currentTraceGraphId()
            if currentTraceGraphId and currentTraceGraphId != currentSpan.traceGraphId:
                self._internalEmitWarning(
                    name="trace.graphId.spanContextMismatch",
                    domain="trace",
                    reason="TraceGraphIdSpanMismatch",
                    message=(
                        "Ambient traceGraphId is different from current span traceGraphId; "
                        "span traceGraphId is authoritative"
                    ),
                    attrs={
                        "ambientTraceGraphId": currentTraceGraphId,
                        "spanTraceGraphId": currentSpan.traceGraphId,
                        "spanId": currentSpan.spanId,
                    },
                )
                _traceGraphIdVar.set(currentSpan.traceGraphId)
            return currentSpan.traceGraphId
        
        tg = self._currentTraceGraphId()
        if tg:
            return tg
        tg = uuidv7(prefix="tg_")
        _traceGraphIdVar.set(tg)
        return tg
    
    # ----- Error helpers -----
    
    def errorFromException(self, err: BaseException) -> TraceError:
        stack = "".join(traceback.format_exception(type(err), err, err.__traceback__))
        return TraceError(type=type(err).__name__, message=str(err), stack=stack)
    
    # ----- Source capture (optional) -----
    
    def _captureSourceInfo(self, skip: int = 0) -> JsonDict:
        # Warning: stack introspection is expensive. Only enabled when requested.
        try:
            frame = inspect.currentframe()
            # current -> _captureSourceInfo -> _buildRecord -> traceEvent/startSpan -> caller
            steps = 3 + skip
            while frame is not None and steps > 0:
                frame = frame.f_back
                steps -= 1
            if frame is None:
                return {"__sourceCaptureFailed__": True}
            return {
                "file": frame.f_code.co_filename,
                "line": int(frame.f_lineno),
                "function": frame.f_code.co_name,
                "module": frame.f_globals.get("__name__", None),
            }
        except Exception:
            return {"__sourceCaptureFailed__": True}
    
    # ----- Internal "safe warning" emit (no further warning) -----
    
    def _internalEmitWarning(
        self,
        *,
        name: str,
        domain: str,
        reason: str,
        message: str,
        attrs: JsonDict | None = None,
    ) -> None:
        depth = _internalEmitDepthVar.get()
        if depth >= 2:
            return
        newDepth = _internalEmitDepthVar.set(depth + 1)
        try:
            resolved = self._resolveRecordContext(
                span=_UNSET,
                parentRecordId=_UNSET,
                traceGraphId=_UNSET,
                baseCorrelation=None,
                eventCorrelation=None,
                _skipScopeWarnings=True,
                _eventCorrelationUsedIn="event",
            )
            record = self._buildRecord(
                recordType="event",
                name=name,
                level="warning",
                tags=["trace"],
                domain=domain,
                reason=reason,
                message=message,
                traceGraphId=resolved.traceGraphId,
                spanId=resolved.span.spanId if resolved.span is not None else None,
                parentSpanId=resolved.span.parentSpanId if resolved.span is not None else None,
                parentRecordId=resolved.parentRecordId,
                correlation=resolved.correlation,
                attrs=attrs or {},
                error=None,
                source=None,
            )
            self._emit(record)
        finally:
            _internalEmitDepthVar.reset(newDepth)
    
    # ----- resolve_* -----
    
    def _resolveSpan(
        self,
        *,
        span: TraceSpan | None | _UnsetType,
    ) -> tuple[TraceSpan | None, bool]:
        explicitSpanIsProvided = span is not _UNSET
        effSpan: TraceSpan | None = (
            self._currentSpan()
            if span is _UNSET
            else cast(TraceSpan | None, span)
        )
        return effSpan, explicitSpanIsProvided
    
    def _resolveParentRecordId(
        self,
        *,
        parentRecordId: str | None | _UnsetType,
    ) -> str | None:
        return (
            self._currentParentRecordId()
            if parentRecordId is _UNSET
            else cast(str | None, parentRecordId)
        )
    
    def _resolveTraceGraphIdForRecord(
        self,
        *,
        span: TraceSpan | None,
        explicitSpanIsProvided: bool,
        traceGraphId: str | _UnsetType,
    ) -> str:
        if explicitSpanIsProvided and span is not None:
            if traceGraphId is not _UNSET:
                if not isinstance(traceGraphId, str) or not traceGraphId:
                    raise ValueError("traceGraphId must be a non-empty string")
                if traceGraphId != span.traceGraphId:
                    raise ValueError(
                        "traceGraphId cannot differ from explicit span.traceGraphId "
                        f"(traceGraphId={traceGraphId!r}, span.traceGraphId={span.traceGraphId!r})"
                    )
            effectiveTraceGraphId = span.traceGraphId
            ambientTraceGraphId = self._currentTraceGraphId()
            if ambientTraceGraphId and ambientTraceGraphId != effectiveTraceGraphId:
                self._internalEmitWarning(
                    name="trace.graphId.explicitSpanMismatch",
                    domain="trace",
                    reason="ExplicitSpanTraceGraphMismatch",
                    message=(
                        "Explicit span traceGraphId differs from ambient traceGraphId; "
                        "record uses explicit span traceGraphId"
                    ),
                    attrs={
                        "ambientTraceGraphId": ambientTraceGraphId,
                        "spanTraceGraphId": effectiveTraceGraphId,
                    },
                )
            return effectiveTraceGraphId

        if traceGraphId is not _UNSET:
            if not isinstance(traceGraphId, str) or not traceGraphId:
                raise ValueError("traceGraphId must be a non-empty string")
            return traceGraphId
        
        return self.ensureTraceGraphId()
    
    def _resolveCorrelation(
        self,
        *,
        baseCorrelation: Mapping[str, Any] | None,
        eventCorrelation: Mapping[str, Any] | None,
        _skipScopeWarnings: bool,
        _eventCorrelationUsedIn: CorrelationScope,
    ) -> Mapping[str, Any]:
        corr = dict(baseCorrelation) if baseCorrelation is not None else self._currentCorrelation()
        
        if eventCorrelation:
            if not _skipScopeWarnings:
                mismatches = self._collectCorrelationScopeMismatches(
                    eventCorrelation,
                    usedIn=_eventCorrelationUsedIn,
                )
                if mismatches:
                    self._internalEmitWarning(
                        name="trace.correlation.scopeMismatch",
                        domain="trace",
                        reason="CorrelationScopeMismatch",
                        message="Correlation keys used in a scope different than registry scopeDefault",
                        attrs={"mismatches": mismatches},
                    )
            for key, value in eventCorrelation.items():
                self._validateCorrelationKV(key, value)
                corr[key] = value
        
        return MappingProxyType(dict(corr))
    
    def _resolveRecordContext(
        self,
        *,
        span: TraceSpan | None | _UnsetType,
        parentRecordId: str | None | _UnsetType,
        traceGraphId: str | _UnsetType,
        baseCorrelation: Mapping[str, Any] | None,
        eventCorrelation: Mapping[str, Any] | None,
        _skipScopeWarnings: bool = False,
        _eventCorrelationUsedIn: CorrelationScope = "event",
    ) -> ResolvedRecordContext:
        effSpan, explicitSpanIsProvided = self._resolveSpan(span=span)
        if explicitSpanIsProvided and effSpan is not None and parentRecordId is _UNSET:
            effParentRecordId = effSpan.spanStartRecordId
        else:
            effParentRecordId = self._resolveParentRecordId(parentRecordId=parentRecordId)
        
        effBaseCorrelation = baseCorrelation
        if effBaseCorrelation is None and explicitSpanIsProvided and effSpan is not None:
            effBaseCorrelation = effSpan.correlation
        
        effTraceGraphId = self._resolveTraceGraphIdForRecord(
            span=effSpan,
            explicitSpanIsProvided=explicitSpanIsProvided,
            traceGraphId=traceGraphId,
        )
        effCorrelation = self._resolveCorrelation(
            baseCorrelation=effBaseCorrelation,
            eventCorrelation=eventCorrelation,
            _skipScopeWarnings=_skipScopeWarnings,
            _eventCorrelationUsedIn=_eventCorrelationUsedIn,
        )
        return ResolvedRecordContext(
            traceGraphId=effTraceGraphId,
            span=effSpan,
            parentRecordId=effParentRecordId,
            correlation=effCorrelation
        )
    
    def _resolveSpanStartContext(
        self,
        *,
        parentRecordId: str | None | _UnsetType,
        traceGraphId: str | None,
        spanCorrelation: Mapping[str, Any] | None,
    ) -> ResolvedSpanStartContext:
        parentSpan = self._currentSpan()
        currentTraceGraphId = self._currentTraceGraphId()
        
        if traceGraphId is not None and (not isinstance(traceGraphId, str) or not traceGraphId):
            raise ValueError("traceGraphId must be a non-empty string")

        if parentSpan is not None:
            if traceGraphId is not None and traceGraphId != parentSpan.traceGraphId:
                self._internalEmitWarning(
                    name="trace.graphId.nestedSpanOverrideIgnored",
                    domain="trace",
                    reason="NestedSpanTraceGraphOverrideIgnored",
                    message=(
                        "Nested span tried to override parent span's traceGraphId; "
                        "parent span's traceGraphId is authoritative"
                    ),
                    attrs={
                        "requestedTraceGraphId": traceGraphId,
                        "parentTraceGraphId": parentSpan.traceGraphId,
                        "parentSpanId": parentSpan.spanId,
                    },
                )
            chosenTraceGraphId = parentSpan.traceGraphId
        elif traceGraphId is not None:
            chosenTraceGraphId = traceGraphId
        elif currentTraceGraphId is not None:
            chosenTraceGraphId = currentTraceGraphId
        else:
            chosenTraceGraphId = uuidv7(prefix="tg_")
        
        base = self._currentCorrelation()
        if spanCorrelation:
            mismatches = self._collectCorrelationScopeMismatches(spanCorrelation, usedIn="span")
            if mismatches:
                self._internalEmitWarning(
                    name="trace.correlation.scopeMismatch",
                    domain="trace",
                    reason="CorrelationScopeMismatch",
                    message="Correlation keys used in a scope different than registry scopeDefault",
                    attrs={"mismatches": mismatches},
                )
            for key, value in spanCorrelation.items():
                self._validateCorrelationKV(key, value)
                base[key] = value
        
        return ResolvedSpanStartContext(
            traceGraphId=chosenTraceGraphId,
            parentSpan=parentSpan,
            parentRecordId=self._resolveParentRecordId(parentRecordId=parentRecordId),
            correlation=MappingProxyType(dict(base)),
        )
    
    def _resolveSpanEndContext(
        self,
        *,
        span: TraceSpan,
    ) -> ResolvedRecordContext:
        return ResolvedRecordContext(
            traceGraphId=span.traceGraphId,
            span=span,
            parentRecordId=span.spanStartRecordId,
            correlation=span.correlation,
        )
    
    def _resolveSpanEndOutcome(
        self,
        outcome: TraceOutcome,
        *,
        durationMs: float,
    ) -> ResolvedSpanEndOutcome:
        baseAttrs = dict(outcome.attrs) if outcome.attrs else {}
        baseAttrs.setdefault("durationMs", durationMs)
        baseAttrs.setdefault("status", outcome.kind)
        
        if isinstance(outcome, TraceOkOutcome):
            return ResolvedSpanEndOutcome(
                status="ok",
                level=_normalizeLevel(outcome.level),
                reason=outcome.reason,
                message=outcome.message,
                attrs=MappingProxyType(dict(baseAttrs)),
                error=None,
            )
        
        if isinstance(outcome, TraceErrorOutcome):
            return ResolvedSpanEndOutcome(
                status="error",
                level=_normalizeLevel(outcome.level),
                reason=outcome.reason,
                message=outcome.message,
                attrs=MappingProxyType(dict(baseAttrs)),
                error=outcome.error,
            )
        
        if isinstance(outcome, TraceCancelledOutcome):
            if outcome.cancelCategory is not None:
                baseAttrs.setdefault("cancelCategory", outcome.cancelCategory)
            return ResolvedSpanEndOutcome(
                status="cancelled",
                level=_normalizeLevel(outcome.level),
                reason=outcome.reason,
                message=outcome.message,
                attrs=MappingProxyType(dict(baseAttrs)),
                error=None,
            )
        
        if isinstance(outcome, TraceTimeoutOutcome):
            if outcome.timeoutMs is not None:
                baseAttrs.setdefault("timeoutMs", outcome.timeoutMs)
            return ResolvedSpanEndOutcome(
                status="timeout",
                level=_normalizeLevel(outcome.level),
                reason=outcome.reason,
                message=outcome.message,
                attrs=MappingProxyType(dict(baseAttrs)),
                error=None,
            )
        
        if isinstance(outcome, TraceDebuggerIntervenedOutcome):
            if outcome.debuggerAction is not None:
                baseAttrs.setdefault("debuggerAction", outcome.debuggerAction)
            if outcome.debuggerClientId is not None:
                baseAttrs.setdefault("debuggerClientId", outcome.debuggerClientId)
            if outcome.requestId is not None:
                baseAttrs.setdefault("requestId", outcome.requestId)
            return ResolvedSpanEndOutcome(
                status="debuggerIntervened",
                level=_normalizeLevel(outcome.level),
                reason=outcome.reason,
                message=outcome.message,
                attrs=MappingProxyType(dict(baseAttrs)),
                error=None,
            )
        
        if isinstance(outcome, TracePolicyRefusedOutcome):
            if outcome.policyId is not None:
                baseAttrs.setdefault("policyId", outcome.policyId)
            if outcome.refusalCategory is not None:
                baseAttrs.setdefault("refusalCategory", outcome.refusalCategory)
            return ResolvedSpanEndOutcome(
                status="policyRefused",
                level=_normalizeLevel(outcome.level),
                reason=outcome.reason,
                message=outcome.message,
                attrs=MappingProxyType(dict(baseAttrs)),
                error=None,
            )
        
        raise TypeError(f"Unsupported TraceOutcome: {type(outcome).__name__}")
    
    # ----- Pure record building -----
    
    def _buildRecord(
        self,
        *,
        recordType: str,
        name: str,
        level: str,
        tags: list[str],
        domain: str | None,
        reason: str | None,
        message: str | None,
        traceGraphId: str | _UnsetType,
        spanId: str | None,
        parentSpanId: str | None,
        parentRecordId: str | None,
        correlation: Mapping[str, Any],
        attrs: JsonDict | None,
        error: TraceError | None,
        source: JsonDict | None,
    ) -> JsonDict:
        if recordType not in ("spanStart", "spanEnd", "event"):
            raise ValueError(
                f"Invalid recordType: {recordType!r}. Only 'spanStart', 'spanEnd' and 'event' are supported."
            )
        
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        
        record: JsonDict = {
            "traceRecordId": uuidv7(prefix="tr_"),
            "traceGraphId": traceGraphId,
            "recordType": recordType,
            "spanId": spanId,
            "parentSpanId": parentSpanId,
            "parentRecordId": parentRecordId,
            "name": name,
            "level": _normalizeLevel(level),
            "tags": list(tags),
            "domain": domain,
            "reason": reason,
            "message": message,
            "correlation": dict(correlation) if correlation else {},
            "attrs": dict(attrs) if isinstance(attrs, dict) else {},
            "error": error.toJson() if error is not None else None,
            "timeUnixMs": _utcNowUnixMs(),
            "seq": self._nextSeq(),
        }
        
        if source is not None:
            record["source"] = source
        
        for key, value in record["correlation"].items():
            spec = self.registry.getCorrelationKeySpec(key)
            if spec is not None and spec.promoteTopLevel and _isScalar(value):
                record[key] = value
        
        return record
    
    def _emit(self, record: JsonDict) -> None:
        try:
            self.hub.emit(record)
        except Exception:
            # Tracing must not crash Turnix
            pass
    
    # ----- Spans -----
    
    def startSpan(
        self,
        spanName: str,
        *,
        level: str | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        reason: str | None = None,
        message: str | None = None,
        attrs: JsonDict | None = None,
        spanCorrelation: JsonDict | None = None,
        eventCorrelation: JsonDict | None = None,
        parentRecordId: str | None | _UnsetType = _UNSET,
        traceGraphId: str | None = None,
        captureSource: bool = False,
    ) -> SpanScope:
        """
        Start a span and emit a spanStart record.
        
        Causal DAG rule:
        - spanStart.parentRecordId defaults to current parentRecordIdVar (if any)
        - spanStart becomes the new parentRecordId for events inside this span
        
        Correlation rule:
        - spanCorrelation is merged into ambient base
        - spanStart record uses that merged result
        - that merged result becomes the new ambient correlation for this span's lifetime
        
        eventCorrelation on spans is allowed but discouraged; it's treated as record-local overlay.
        """
        if not isinstance(spanName, str) or not spanName:
            raise ValueError("spanName must be a non-empty string")
        
        resolved = self._resolveSpanStartContext(
            parentRecordId=parentRecordId,
            traceGraphId=traceGraphId,
            spanCorrelation=spanCorrelation,
        )
        
        source = self._captureSourceInfo(skip=0) if captureSource else None
        
        spanId = uuidv7(prefix="sp_")
        startRecord = self._buildRecord(
            recordType="spanStart",
            name=spanName,
            level=level or "info",
            tags=tags or [],
            domain=domain,
            reason=reason,
            message=message,
            traceGraphId=resolved.traceGraphId,
            spanId=spanId,
            parentSpanId=resolved.parentSpan.spanId if resolved.parentSpan is not None else None,
            parentRecordId=resolved.parentRecordId,
            correlation=self._resolveCorrelation(
                baseCorrelation=resolved.correlation,
                eventCorrelation=eventCorrelation,
                _skipScopeWarnings=False,
                _eventCorrelationUsedIn="event",
            ),
            attrs=attrs,
            error=None,
            source=source,
        )
        
        spanStartRecordId = str(startRecord.get("traceRecordId", ""))
        
        baseCorrSnapshot = dict(resolved.correlation)
        spanCorr = MappingProxyType(dict(baseCorrSnapshot))
        
        span = TraceSpan(
            traceGraphId=resolved.traceGraphId,
            spanId=spanId,
            spanName=spanName,
            parentSpanId=resolved.parentSpan.spanId if resolved.parentSpan is not None else None,
            spanStartRecordId=spanStartRecordId,
            correlation=spanCorr,
        )
        
        # Install ambient context for this Task; tokens are owned by SpanScope.
        spanToken = _spanVar.set(span)
        parentRecordIdToken = _parentRecordIdVar.set(spanStartRecordId)
        traceGraphIdToken = _traceGraphIdVar.set(resolved.traceGraphId)
        
        # IMPORTANT: correlation for children becomes spanCorrelation-merged base snapshot.
        # This ensures callers don't need to call updateCorrelation manually for every boundary.
        correlationToken = _correlationVar.set(dict(baseCorrSnapshot))
        
        task = self._safeCurrentTask()
        
        self._emit(startRecord)
        
        return SpanScope(
            tracer=self,
            span=span,
            _spanToken=spanToken,
            _parentRecordIdToken=parentRecordIdToken,
            _traceGraphIdToken=traceGraphIdToken,
            _correlationToken=correlationToken,
            _task=task,
        )
    
    def _emitSpanEnd(
        self,
        span: TraceSpan,
        *,
        outcome: TraceOutcome,
        tags: list[str] | None,
        domain: str | None,
    ) -> None:
        durationMs = float(_utcNowUnixMs() - span.startUnixMs)
        resolvedOutcome = self._resolveSpanEndOutcome(outcome, durationMs=durationMs)
        
        resolved = self._resolveSpanEndContext(span=span)
        endRecord = self._buildRecord(
            recordType="spanEnd",
            name=span.spanName,
            level=resolvedOutcome.level,
            tags=tags or [],
            domain=domain,
            reason=resolvedOutcome.reason,
            message=resolvedOutcome.message,
            traceGraphId=resolved.traceGraphId,
            spanId=span.spanId,
            parentSpanId=span.parentSpanId,
            parentRecordId=resolved.parentRecordId,
            correlation=resolved.correlation,
            attrs=dict(resolvedOutcome.attrs),
            error=resolvedOutcome.error,
            source=None,
        )
        endRecord["spanStartRecordId"] = span.spanStartRecordId
        
        self._emit(endRecord)

    # ----- Events -----
    
    def traceEvent(
        self,
        eventName: str,
        *,
        level: str | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        reason: str | None = None,
        message: str | None = None,
        attrs: JsonDict | None = None,
        eventCorrelation: JsonDict | None = None,
        parentRecordId: str | None | _UnsetType = _UNSET,
        span: TraceSpan | None | _UnsetType = _UNSET,
        error: TraceError | None = None,
        captureSource: bool = False,
    ) -> str:
        """
        Emit a point event record. Returns traceRecordId.
        - base correlation is ambient (span correlation already included)
        - eventCorrelation overlays record only; does not update ambient
        """
        if not isinstance(eventName, str) or not eventName:
            raise ValueError("eventName must be a non-empty string")
        
        spec = self.registry.getEventSpec(eventName)
        effDomain = domain or (spec.domain if spec else None)
        effLevel = _normalizeLevel(level or (spec.defaultLevel if spec else "debug"))
        
        if spec and spec.requiredReason and not reason:
            # Registry-defined policy: allow emit, but make the missing reason explicit.
            reason = "MissingReason"
        
        resolved = self._resolveRecordContext(
            span=span,
            parentRecordId=parentRecordId,
            traceGraphId=_UNSET,
            baseCorrelation=None,
            eventCorrelation=eventCorrelation,
            _skipScopeWarnings=False,
            _eventCorrelationUsedIn="event",
        )
        
        source = self._captureSourceInfo(skip=0) if captureSource else None
        
        record = self._buildRecord(
            recordType="event",
            name=eventName,
            level=effLevel,
            tags=tags or [],
            domain=effDomain,
            reason=reason,
            message=message,
            traceGraphId=resolved.traceGraphId,
            spanId=resolved.span.spanId if resolved.span is not None else None,
            parentSpanId=resolved.span.parentSpanId if resolved.span is not None else None,
            parentRecordId=resolved.parentRecordId,
            correlation=resolved.correlation,
            attrs=attrs,
            error=error,
            source=source,
        )
        
        self._emit(record)
        return str(record.get("traceRecordId", ""))
    
    # ----- Task spawner with tracing support -----
    
    def createTask(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        taskName: str | None = None,
        spanName: str | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        spanCorrelation: JsonDict | None = None,
        parentRecordId: str | None | _UnsetType = _UNSET,
    ) -> asyncio.Task[Any]:
        """
        Robust task creation:
        - explicit context propagation via contextvars.copy_context()
        - optional wrapper span in the new task
        - if wrapper span exists, its end is guaranteed even on exception
        """
        ctx = contextvars.copy_context()
        
        async def _runner() -> Any:
            if spanName:
                scope = self.startSpan(
                    spanName,
                    level="info",
                    tags=tags or [],
                    domain=domain,
                    spanCorrelation=spanCorrelation,
                    parentRecordId=parentRecordId,
                )
                try:
                    result = await coro
                except asyncio.CancelledError as err:
                    scope.cancel(
                        reason="TaskCancelled",
                        message=str(err) or "Task was cancelled",
                        cancelCategory="asyncio",
                        domain=domain,
                    )
                    raise
                except Exception as err:
                    scope.fail(
                        reason="TaskFailed",
                        message=str(err),
                        error=self.errorFromException(err),
                        domain=domain,
                    )
                    raise
                else:
                    scope.ok(domain=domain)
                    return result
        
            # No wrapper span required; just run the coroutine
            return await coro
        
        # Ensure the task is created under the captured context.
        def _create() -> asyncio.Task[Any]:
            return asyncio.create_task(_runner(), name=taskName)
        
        return ctx.run(_create)
    
    # ----- KernelRun span convenience -----
    
    def getKernelRootSpanScope(self) -> SpanScope | None:
        with self._kernelRootLock:
            scope = self._kernelRootScope
            return None if scope is None or scope._ended else scope
    
    def startKernelRootSpan(
        self,
        *,
        traceGraphId: str | None = None
    ) -> SpanScope:
        """
        Start the KernelRun root span.
        """
        with self._kernelRootLock:
            existingScope = self._kernelRootScope
            if existingScope is not None and not existingScope._ended:
                return existingScope
            
            currentSpan = self._currentSpan()
            if currentSpan is not None:
                raise RuntimeError(
                    "KernelRunSpan must be the root span and cannot be started inside another span "
                    f"(current spanId={currentSpan.spanId!r}, spanName={currentSpan.spanName!r})"
                )
        
        effectiveTraceGraphId = traceGraphId or uuidv7(prefix="tg_")
        
        scope = self.startSpan(
            "KernelRunSpan",
            level="info",
            tags=["kernelRun"],
            domain="trace",
            reason="KernelRunSpanBegin",
            traceGraphId=effectiveTraceGraphId,
            captureSource=True,
        )
        self._kernelRootScope = scope
        
        self.event("kernelRun.start") \
            .level("info") \
            .tags("kernelRun") \
            .domain("trace") \
            .emit()
        
        return scope
    
    def endKernelRootSpan(
        self,
        *,
        status: str = "ok",
        endReasonCategory: str = "NormalExit",
    ) -> None:
        """
        End the KernelRun root span.
        """
        with self._kernelRootLock:
            scope = self._kernelRootScope
            if scope is None or scope._ended:
                self._kernelRootScope = None
                return

        if not self._isCurrentTaskOwner(scope):
            self.event("trace.kernelRoot.crossTaskAttemptedEnd") \
                .level("warning") \
                .domain("trace") \
                .reason("CrossTaskAttemptedKernelRootEnd") \
                .message(
                    "KernelRun root span end was attempted from a different asyncio Task; "
                    "owner task must end the span") \
                .attr("taskCreatedId", id(scope._task)) \
                .attr("taskAttemptedId", id(self._safeCurrentTask())) \
                .span(scope.span) \
                .emit()
            return
        
        self.event("kernelRun.end") \
            .level("info") \
            .tags("kernelRun") \
            .domain("trace") \
            .attr("status", status) \
            .attr("endReasonCategory", endReasonCategory) \
            .emit()
        
        try:
            if status == "ok":
                outcome: TraceOutcome = TraceOkOutcome(
                    reason="KernelRunSpanEnd",
                    attrs={"endReasonCategory": endReasonCategory},
                )
            elif status == "cancelled":
                outcome: TraceOutcome = TraceCancelledOutcome(
                    reason="KernelRunSpanEnd",
                    cancelCategory=endReasonCategory,
                )
            elif status == "timeout":
                outcome: TraceOutcome = TraceTimeoutOutcome(
                    reason="KernelRunSpanEnd",
                    attrs={"endReasonCategory": endReasonCategory},
                )
            elif status == "policyRefused":
                outcome: TraceOutcome = TracePolicyRefusedOutcome(
                    reason="KernelRunSpanEnd",
                    refusalCategory=endReasonCategory,
                )
            elif status == "debuggerIntervened":
                outcome: TraceOutcome = TraceDebuggerIntervenedOutcome(
                    reason="KernelRunSpanEnd",
                    debuggerAction=endReasonCategory,
                )
            else:
                outcome: TraceOutcome = TraceErrorOutcome(
                    reason="KernelRunSpanEnd",
                    message=f"Kernel root ended with status {status!r}",
                    attrs={"endReasonCategory": endReasonCategory},
                )
            
            scope.end(
                outcome=outcome,
                domain="trace",
            )
        finally:
            self._kernelRootScope = None


# -----------------------------------------------------------------------------
# Tracer (kernel-tier)
# -----------------------------------------------------------------------------
_globalHub = TraceHub()
_globalRegistry = TraceRegistry()

# Default correlation allowlist (promote/index tuned: promote narrowly, allow broadly)
# Note: taskCreatedId/taskEndedId are NOT correlation (attrs only) to avoid propagation.
for _key, _promote, _scope in (
    ("kernelRunId", True, "span"),
    ("appInstanceId", True, "span"),
    ("appPackId", True, "span"),
    ("sessionId", True, "span"),
    ("pipelineId", True, "span"),
    ("pipelineRunId", True, "span"),
    ("orchestrationUnitId", True, "span"),
    ("transactionId", True, "span"),
    ("memoryLayerId", True, "span"),
    ("viewId", True, "span"),
    ("clientId", True, "span"),
    ("modId", True, "span"),
    ("hookId", True, "span"),
    ("rpcKind", True, "event"),
    ("llmProvider", False, "event"),
    ("llmPreset", False, "event"),
):
    _globalRegistry.registerCorrelationKey(
        _key,
        promoteTopLevel=_promote,
        index=True,
        scopeDefault=_scope, # type: ignore[arg-type]
    )

# Few useful event declarations
_globalRegistry.registerEvent("kernelRun.start", domain="trace", defaultLevel="info")
_globalRegistry.registerEvent("kernelRun.end", domain="trace", defaultLevel="info")
_globalRegistry.registerEvent("trace.correlation.scopeMismatch", domain="trace", defaultLevel="warning")
_globalRegistry.registerEvent("trace.graphId.spanContextMismatch", domain="trace", defaultLevel="warning")
_globalRegistry.registerEvent("trace.graphId.explicitSpanMismatch", domain="trace", defaultLevel="warning")
_globalRegistry.registerEvent("trace.graphId.nestedSpanOverrideIgnored", domain="trace", defaultLevel="warning")
_globalRegistry.registerEvent("trace.kernelRoot.crossTaskAttemptedEnd", domain="trace", defaultLevel="warning")
_globalRegistry.registerEvent("trace.span.crossTaskAttemptedEnd", domain="trace", defaultLevel="warning")
_globalRegistry.registerEvent("rpc.hello.accepted", domain="rpc", defaultLevel="info")
_globalRegistry.registerEvent("rpc.clientReady.accepted", domain="rpc", defaultLevel="info")

_globalTracer = Tracer(_globalHub, _globalRegistry)


def getTraceHub() -> TraceHub:
    return _globalHub


def getTraceRegistry() -> TraceRegistry:
    return _globalRegistry


def getTracer() -> Tracer:
    return _globalTracer
