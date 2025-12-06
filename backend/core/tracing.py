# backend/core/tracing.py
from __future__ import annotations
import asyncio
from asyncio import AbstractEventLoop
import contextvars
import datetime as dt
import threading
from dataclasses import dataclass, field
from typing import Any

from backend.core.ids import uuidv7

JsonDict = dict[str, Any]



def _utcNowIso() -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")



_spanContextVar: contextvars.ContextVar["TraceSpan | None"] = contextvars.ContextVar(
    "turnix_current_span",
    default=None,
)



_traceContextVar: contextvars.ContextVar[JsonDict] = contextvars.ContextVar(
    "turnix_trace_context",
    default={},
)



@dataclass
class TraceSpan:
    traceId: str
    spanId: str
    parentSpanId: str | None
    spanName: str
    context: JsonDict = field(default_factory=dict)
    startTime: float = field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc).timestamp()
    )



def _queuePutNowait(queue: asyncio.Queue[JsonDict], record: JsonDict) -> None:
    """
    Helper used with loop.call_soon_threadsafe().
    Safe to call from any thread; drops records if the subscriber is slow.
    """
    try:
        queue.put_nowait(record)
    except asyncio.QueueFull:
        # Slow subscribers just drop events. Tracing must not block.
        pass


class TraceHub:
    """
    In-memory ring buffer + live subscribers.
    
    - emit(record): append to buffer, fan-out to subscriber queues
    - subscribe(): returns (snapshot, queue)
    - unsubscribe(queue): detach subscriber
    """
    def __init__(self, capacity: int = 5000) -> None:
        self.capacity = max(1, capacity)
        self._buffer: list[JsonDict] = []
        # Map subscriber queues to the event loop that owns them.
        # This lets emit() schedule puts from any thread safely.
        self._subscribers: dict[asyncio.Queue[JsonDict], AbstractEventLoop] = {}
        self._lock = threading.Lock()
    
    def emit(self, record: JsonDict) -> None:
        """
        Emit a record:
        - append to ring buffer
        - fan out to all subscribers via their owning event loops
        This method is safe to call from any thread.
        """
        # Keep this as cheap and safe as possible.
        with self._lock:
            if len(self._buffer) >= self.capacity:
                self._buffer.pop(0)
            self._buffer.append(record)
            subscribers = list(self._subscribers.items())
        
        for queue, loop in subscribers:
            try:
                # Schedule delivery on the subscriber's event loop.
                loop.call_soon_threadsafe(_queuePutNowait, queue, record)
            except RuntimeError:
                # Loop likely closed. Drop this subscriber.
                with self._lock:
                    self._subscribers.pop(queue, None)
    
    def subscribe(self) -> tuple[list[JsonDict], asyncio.Queue[JsonDict]]:
        """
        Returns (snapshot, queue):
        - snapshot: current buffer copy (for initial dump)
        - queue: receives future records
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[JsonDict] = asyncio.Queue(maxsize=1000)
        with self._lock:
            snapshot = list(self._buffer)
            self._subscribers[queue] = loop
        return snapshot, queue
    
    def unsubscribe(self, queue: asyncio.Queue[JsonDict]) -> None:
        with self._lock:
            self._subscribers.pop(queue, None)



class Tracer:
    """
    Core tracer implementation.
    
    - Uses contextvars to track current span + trace context.
    - Emits JSON-serializable dicts to TraceHub.
    - Never raises out of emit().
    """
    
    def __init__(self, hub: TraceHub | None = None) -> None:
        self.hub = hub or TraceHub()
        self._seq = 0
        self._seqLock = threading.Lock()
        self.rootSpan: TraceSpan | None = None
    
    # ----- Context / Bookkeeping -----
    
    def _nextSeq(self) -> int:
        with self._seqLock:
            self._seq += 1
            return self._seq
    
    def _currentSpan(self) -> TraceSpan | None:
        return _spanContextVar.get(None)

    def _currentContext(self) -> JsonDict:
        # Always clone so callers cannot mutate shared dict.
        return dict(_traceContextVar.get({}))
    
    def updateTraceContext(self, values: JsonDict) -> None:
        """
        Merge values into the ambient trace context for this task.
        Future spans/events will see these keys.
        """
        current = self._currentContext()
        current.update(values)
        _traceContextVar.set(current)
    
    def _buildBaseRecord(
        self,
        recordType: str,
        span: TraceSpan | None,
        level: str,
        tags: list[str] | None,
        attrs: JsonDict | None = None,
    ) -> JsonDict:
        ctx = self._currentContext()
        if span is not None:
            # Span context overrides ambient context where they overlap.
            ctx = {**ctx, **span.context}
        
        # Remove _ctxToken if present (we don't want it in the trace record)
        if "_ctxToken" in ctx:
            del ctx["_ctxToken"]
        
        record: JsonDict = {
            "recordType": recordType,
            "time": _utcNowIso(),
            "seq": self._nextSeq(),
            "traceId": span.traceId if span is not None else ctx.get("traceId", ""),
            "spanId": span.spanId if span is not None else ctx.get("spanId", ""),
            "level": level,
            "tags": tags or [],
            "attrs": {**ctx, **(attrs or {})},
        }
        
        # Copy known context keys to top-level for easy filtering in the viewer.
        for key in (
            "appInstanceId",
            "appPackId",
            "sessionId",
            "pipelineId",
            "pipelineRunId",
            "viewId",
            "clientId",
            "modId",
            "hookId",
            "rpcKind",
            "llmProvider",
            "llmPreset",
        ):
            if key in ctx:
                record[key] = ctx[key]
        
        return record
    
    # ----- Spans -----
    
    def startSpan(
        self,
        spanName: str,
        attrs: JsonDict | None = None,
        level: str = "info",
        tags: list[str] | None = None,
        contextOverrides: JsonDict | None = None
    ) -> TraceSpan:
        """
        Start a span, set it as current for this task, and emit spanStart.
        """
        parent = self._currentSpan()
        baseCtx = self._currentContext()
        if contextOverrides:
            baseCtx.update(contextOverrides)
        
        traceId = baseCtx.get("traceId") or uuidv7(prefix="trace_")
        spanId = uuidv7(prefix="span_")
        spanCtx = {**baseCtx, "traceId": traceId, "spanId": spanId}
        
        span = TraceSpan(
            traceId=traceId,
            spanId=spanId,
            parentSpanId=parent.spanId if parent is not None else None,
            spanName=spanName,
            context=spanCtx,
        )
        
        token = _spanContextVar.set(span)
        # Remember token so we can restore previous span when ending.
        span.context["_ctxToken"] = token
        
        record = self._buildBaseRecord("spanStart", span, level, tags or [], attrs)
        record["spanName"] = spanName
        record["parentSpanId"] = span.parentSpanId
        record["status"] = None
        
        self._emit(record)
        return span
    
    def endSpan(
        self,
        span: TraceSpan,
        status: str = "ok",
        *,
        level: str = "info",
        tags: list[str] | None = None,
        errorType: str | None = None,
        errorMessage: str | None = None,
        errorStack: str | None = None,
        attrs: JsonDict | None = None,
    ) -> None:
        """
        End a span, restore previous current span, and emit spanEnd.
        """
        # Prevent double endSpan calls for the same span
        if span.context.get("_ended") is True:
            return
        span.context["_ended"] = True
        
        token = span.context.get("_ctxToken")
        if token is not None:
            try:
                _spanContextVar.reset(token)
            except Exception:
                # If reset fails, don't break tracing...
                pass
        
        record = self._buildBaseRecord("spanEnd", span, level, tags or [], attrs)
        record["spanName"] = span.spanName
        record["status"] = status
        record["errorType"] = errorType
        record["errorMessage"] = errorMessage
        record["errorStack"] = errorStack
        
        if attrs is None:
            attrs = {}
        
        durationMs = (dt.datetime.now(dt.timezone.utc).timestamp() - span.startTime) * 1000.0
        attrs.setdefault("durationMs", durationMs)
        record["attrs"] = attrs
        
        self._emit(record)
    
    # ----- Events -----

    def traceEvent(
        self,
        eventName: str,
        attrs: JsonDict | None = None,
        *,
        level: str = "debug",
        tags: list[str] | None = None,
        span: TraceSpan | None = None,
    ) -> None:
        """
        Emit an event attached to the given span or current span.
        """
        if span is None:
            span = self._currentSpan()
        
        record = self._buildBaseRecord("event", span, level, tags or [], attrs)
        record["eventName"] = eventName
        
        self._emit(record)
    
    # ----- Process root span -----
    
    def startProcessSpan(self, attrs: JsonDict | None = None) -> TraceSpan:
        """
        Convenience: create the root 'process.turnix' span and process.start event.
        If already started, returns existing root span.
        """
        if self.rootSpan is not None:
            return self.rootSpan
        
        span = self.startSpan(
            "process.turnix",
            attrs=attrs or {},
            level="info",
            tags=["process"],
        )
        self.rootSpan = span
        
        self.traceEvent("process.start", level="info", tags=["process"], span=span)
        return span
    
    def endProcessSpan(
        self,
        status: str = "ok",
        *,
        errorType: str | None = None,
        errorMessage: str | None = None,
        errorStack: str | None = None,
        attrs: JsonDict | None = None,
    ) -> None:
        """
        Convenience: close the root process span with process.stop event.
        Safe to call multiple times, only first call does anything.
        """
        span = self.rootSpan
        if span is None:
            return
        
        self.traceEvent(
            "process.stop",
            level="info",
            tags=["process"],
            span=span,
            attrs={"status": status},
        )
        
        self.endSpan(
            span,
            status=status,
            level="info",
            tags=["process"],
            errorType=errorType,
            errorMessage=errorMessage,
            errorStack=errorStack,
            attrs=attrs,
        )
        self.rootSpan = None
    
    # ----- Low level -----
    
    def _emit(self, record: JsonDict) -> None:
        try:
            self.hub.emit(record)
        except Exception:
            # Tracing must not crash
            pass



# Global tracer + hub singletons for now.
_globalHub = TraceHub()
_globalTracer = Tracer(_globalHub)



def getTraceHub() -> TraceHub:
    return _globalHub

def getTracer() -> Tracer:
    return _globalTracer
