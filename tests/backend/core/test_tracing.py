# tests/backend/core/test_tracing.py
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

import pytest

from backend.core.tracing import TraceHub, TraceRegistry, Tracer


def _makeTracer() -> Tracer:
    hub = TraceHub(capacity=10000)
    registry = TraceRegistry()
    
    # Minimal allowlist for tests
    for key, promote, scope in (
        ("kernelRunId", True, "span"),
        ("appPackId", True, "span"),
        ("sessionId", True, "span"),
        ("rpcKind", True, "event"),
        ("llmProvider", False, "event"),
    ):
        registry.registerCorrelationKey(key, promoteTopLevel=promote, index=True, scopeDefault=scope) # type: ignore[arg-type]

    # Trace system events
    registry.registerEvent("trace.span.crossTaskAttemptedEnd", domain="trace", defaultLevel="warning")
    registry.registerEvent("trace.kernelRoot.crossTaskAttemptedEnd", domain="trace", defaultLevel="warning")
    registry.registerEvent("trace.graphId.nestedSpanOverrideIgnored", domain="trace", defaultLevel="warning")
    registry.registerEvent("trace.correlation.scopeMismatch", domain="trace", defaultLevel="warning")
    registry.registerEvent("trace.graphId.spanContextMismatch", domain="trace", defaultLevel="warning")
    registry.registerEvent("trace.graphId.explicitSpanMismatch", domain="trace", defaultLevel="warning")
    
    registry.registerEvent("kernelRun.start", domain="trace", defaultLevel="info")
    registry.registerEvent("kernelRun.end", domain="trace", defaultLevel="info")
    
    # EventSpec tests
    registry.registerEvent("evt.defaultSpec", domain="spec", defaultLevel="info")
    registry.registerEvent("evt.requiredReason", domain="spec", defaultLevel="warning", requiredReason=True)

    return Tracer(hub, registry)


async def _drain(
    queue: asyncio.Queue[dict],
    *,
    limit: int = 5000,
) -> list[dict]:
    out: list[dict] = []
    for _ in range(limit):
        try:
            out.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out


async def _flushLoop() -> None:
    # One loop turn to flush call_soon_threadsafe fanout.
    await asyncio.sleep(0)


async def _collect(
    snapshot: list[dict],
    queue: asyncio.Queue[dict],
    *,
    extraFlushes: int = 0,
) -> list[dict]:
    await _flushLoop()
    for _ in range(extraFlushes):
        await _flushLoop()
    return snapshot + await _drain(queue)


def _find(
    records: Iterable[dict],
    *,
    recordType: str,
    name: str,
) -> dict:
    return next(
        record for record in records
        if record.get("recordType") == recordType
        and record.get("name") == name
    )


def _filter(
    records: Iterable[dict],
    *,
    recordType: str | None = None,
    name: str | None = None,
) -> list[dict]:
    out: list[dict] = []
    for record in records:
        if recordType is not None and record.get("recordType") != recordType:
            continue
        if name is not None and record.get("name") != name:
            continue
        out.append(record)
    return out


@pytest.mark.asyncio
async def test_eventSpec_applies_domain_and_default_level() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    tracer.event("evt.defaultSpec").emit()
    
    records = await _collect(snapshot, queue)
    record = _find(records, recordType="event", name="evt.defaultSpec")
    assert record["domain"] == "spec"
    assert record["level"] == "info"


@pytest.mark.asyncio
async def test_eventSpec_requiredReason_sets_missingReason_when_absent() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    tracer.event("evt.requiredReason").emit()
    
    records = await _collect(snapshot, queue)
    record = _find(records, recordType="event", name="evt.requiredReason")
    assert record.get("domain") == "spec"
    assert record.get("level") == "warning"
    assert record.get("reason") == "MissingReason"


@pytest.mark.asyncio
async def test_unknown_correlation_key_raises_key_error() -> None:
    tracer = _makeTracer()
    
    with pytest.raises(KeyError):
        tracer.event("x").corrEvent(unknownKey="nope").emit()


@pytest.mark.asyncio
async def test_non_scalar_correlation_value_raises_type_error() -> None:
    tracer = _makeTracer()
    
    with pytest.raises(TypeError):
        tracer.event("x").corrEvent(llmProvider={"nope": True}).emit()


@pytest.mark.asyncio
async def test_non_scalar_attr_raises_type_error() -> None:
    tracer = _makeTracer()
    
    with pytest.raises(TypeError):
        tracer.event("x").attr("bad", {"x": 1}).emit()


@pytest.mark.asyncio
async def test_attrJson_stores_jsonish_value_and_never_raw_object() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    class _NoJson:
        pass
    
    obj = _NoJson()
    tracer.event("evt.attrJson").attrJson("payload", obj).emit()
    
    records = await _collect(snapshot, queue)
    record = _find(records, recordType="event", name="evt.attrJson")
    
    payload = (record.get("attrs") or {}).get("payload")
    assert payload is not None
    assert payload is not obj
    assert isinstance(payload, (type(None), bool, int, float, str, list, dict))


@pytest.mark.asyncio
async def test_attrJson_on_jsonify_exception_records_jsonifyError_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.core.tracing as tracing
    
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("jsonify exploded")
    
    monkeypatch.setattr(tracing, "tryJSONify", _boom)
    
    tracer.event("evt.attrJson.fail").attrJson("payload", object()).emit()
    
    records = await _collect(snapshot, queue)
    record = _find(records, recordType="event", name="evt.attrJson.fail")

    payload = (record.get("attrs") or {}).get("payload")
    assert isinstance(payload, dict)
    assert payload.get("__jsonifyError__") is True
    assert payload.get("type") == "RuntimeError"


@pytest.mark.asyncio
async def test_parentRecordId_defaults_to_spanStart_inside_span() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    scope = tracer.span("outer").domain("test").start()
    tracer.event("inside").domain("test").emit()
    scope.end()
    
    records = await _collect(snapshot, queue)
    spanStart = _find(records, recordType="spanStart", name="outer")
    inside = _find(records, recordType="event", name="inside")
    assert inside["parentRecordId"] == spanStart["traceRecordId"]


@pytest.mark.asyncio
async def test_parentRecordId_explicit_override_is_respected() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    scope = tracer.span("outer").domain("test").start()
    tracer.traceEvent("inside", domain="test", parentRecordId="trExplicitParent")
    scope.end()
    
    records = await _collect(snapshot, queue)
    inside = _find(records, recordType="event", name="inside")
    assert inside["parentRecordId"] == "trExplicitParent"


@pytest.mark.asyncio
async def test_spanStart_parentRecordId_defaults_to_ambient_parentRecordId() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    root = tracer.span("root").domain("test").start()
    child = tracer.span("child").domain("test").start()
    child.end()
    root.end()

    records = await _collect(snapshot, queue)
    rootStart = _find(records, recordType="spanStart", name="root")
    childStart = _find(records, recordType="spanStart", name="child")
    assert childStart["parentRecordId"] == rootStart["traceRecordId"]


@pytest.mark.asyncio
async def test_explicit_span_on_event_sets_spanId_and_parentSpanId_to_that_span() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    outer = tracer.span("outer").domain("test").start()
    inner = tracer.span("inner").domain("test").start()
    
    # Emit even "as if" it belonged to outer span (explicit span parameter).
    tracer.event("evt.explicitSpan").domain("test").span(outer.span).emit()
    
    inner.end()
    outer.end()
    
    records = await _collect(snapshot, queue)
    record = _find(records, recordType="event", name="evt.explicitSpan")
    assert record["spanId"] == outer.span.spanId
    assert record["parentSpanId"] == outer.span.parentSpanId
    assert record["parentSpanId"] != inner.span.spanId


@pytest.mark.asyncio
async def test_traceGraphId_repaired_if_ambient_differs_from_current_span() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.startSpan("outer", domain="test", traceGraphId="tg_span")
    tracer.setTraceGraphId("tg_wrong")
    
    tracer.event("evt.afterMismatch").domain("test").emit()
    scope.end()
    
    records = await _collect(snapshot, queue)
    warnings = _filter(records, recordType="event", name="trace.graphId.spanContextMismatch")
    assert len(warnings) >= 1
    
    event = _find(records, recordType="event", name="evt.afterMismatch")
    assert event["traceGraphId"] == "tg_span"


@pytest.mark.asyncio
async def test_traceGraphId_explicit_span_mismatch_warning_emitted() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.startSpan("outer", domain="test", traceGraphId="tg_span")
    tracer.setTraceGraphId("tg_wrong")
    
    # Explicit span should win; a warning should be emitted.
    tracer.event("evt.explicitSpanMismatchWarn").domain("test").span(scope.span).emit()

    scope.end()
    
    records = await _collect(snapshot, queue)
    warnings = _filter(records, recordType="event", name="trace.graphId.explicitSpanMismatch")
    assert len(warnings) >= 1
    
    event = _find(records, recordType="event", name="evt.explicitSpanMismatchWarn")
    assert event["traceGraphId"] == "tg_span"


@pytest.mark.asyncio
async def test_setTraceGraphId_outside_span_persists_for_future_events() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    tracer.setTraceGraphId("tg_manual")
    tracer.event("evt.one").domain("test").emit()
    tracer.event("evt.two").domain("test").emit()
    
    records = await _collect(snapshot, queue)
    one = _find(records, recordType="event", name="evt.one")
    two = _find(records, recordType="event", name="evt.two")
    assert one["traceGraphId"] == "tg_manual"
    assert two["traceGraphId"] == "tg_manual"


@pytest.mark.asyncio
async def test_spanEnd_uses_span_correlation_snapshot_even_if_ambient_mutated() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    scope = tracer.span("outer").domain("test").corrSpan(sessionId="session1").start()
    tracer.updateCorrelation({"sessionId": "session2"}) # Mutate ambient inside span
    scope.end()
    
    records = await _collect(snapshot, queue)
    end = _find(records, recordType="spanEnd", name="outer")
    
    # spanEnd uses baseCorrelation=span.correlation (snapshot), not current ambient.
    assert end["correlation"].get("sessionId") == "session1"
    assert end.get("sessionId") == "session1"


@pytest.mark.asyncio
async def test_spanScope_end_is_idempotent_emits_one_spanEnd() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    scope = tracer.span("outer").domain("test").start()
    scope.end()
    scope.end()
    scope.end()
    
    records = await _collect(snapshot, queue)
    ends = _filter(records, recordType="spanEnd", name="outer")
    assert len(ends) == 1


@pytest.mark.asyncio
async def test_traceHub_subscribe_snapshot_contains_prior_records() -> None:
    tracer = _makeTracer()

    # Emit before subscribe
    tracer.event("evt.beforeSub").domain("test").emit()
    await _flushLoop()
    
    snapshot, queue = tracer.hub.subscribe()
    await _flushLoop()
    
    tracer.event("evt.afterSub").domain("test").emit()
    
    records = await _collect(snapshot, queue)
    before = _find(records, recordType="event", name="evt.beforeSub")
    after = _find(records, recordType="event", name="evt.afterSub")
    assert before["name"] == "evt.beforeSub"
    assert after["name"] == "evt.afterSub"


@pytest.mark.asyncio
async def test_traceHub_unsubscribe_stops_delivery_to_that_output() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    tracer.event("evt.one").domain("test").emit()
    await _flushLoop()
    
    tracer.hub.unsubscribe(queue)
    tracer.event("evt.two").domain("test").emit()
    await _flushLoop()

    drained = await _drain(queue)
    names = [record.get("name") for record in (snapshot + drained)]
    assert "evt.one" in names
    assert "evt.two" not in names


@pytest.mark.asyncio
async def test_subscriber_queue_overflow_is_dropped_but_does_not_crash() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    # Do not drain; push a lot to exceed maxsize = 1000
    for ii in range(2500):
        tracer.event(f"ext.flood.{ii}").domain("test").emit()
    
    await _flushLoop()
    drained = await _drain(queue, limit=50_000)
    
    # We should have received "some" but not necessarily all due to overflow
    assert len(snapshot) == 0
    assert 1 <= len(drained) <= 1000


@pytest.mark.asyncio
async def test_spanCorrelation_warns_when_using_event_scoped_key() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("outer").domain("test").corrSpan(llmProvider="openai").start()
    scope.end()
    
    records = await _collect(snapshot, queue)
    warnings = _filter(records, recordType="event", name="trace.correlation.scopeMismatch")
    assert len(warnings) >= 1


@pytest.mark.asyncio
async def test_eventCorrelation_scope_mismatch_warns_when_using_span_scoped_key() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    tracer.event("evt.badScope").domain("test").corrEvent(sessionId="session1").emit()
    
    records = await _collect(snapshot, queue)
    warnings = _filter(records, recordType="event", name="trace.correlation.scopeMismatch")
    assert len(warnings) >= 1


@pytest.mark.asyncio
async def test_createTask_propagates_context_and_wraps_span_when_requested() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    outer = tracer.span("outer").domain("test").corrSpan(sessionId="session1").start()
    
    async def work() -> None:
        tracer.event("evt.fromTask").domain("test").emit()
    
    taskWork = tracer.createTask(
        work(),
        taskName="task1",
        spanName="TaskSpan",
        domain="test",
        spanCorrelation={"rpcKind": "hello"},
    )
    await taskWork
    outer.end()
    
    records = await _collect(snapshot, queue)

    evt = _find(records, recordType="event", name="evt.fromTask")
    assert evt["correlation"].get("sessionId") == "session1" # Propagated from outer context
    assert evt["correlation"].get("rpcKind") == "hello" # From wrapper's spanCorrelation
    assert evt.get("sessionId") == "session1"
    assert evt.get("rpcKind") == "hello"

    taskSpanStart = _find(records, recordType="spanStart", name="TaskSpan")
    taskSpanEnd = _find(records, recordType="spanEnd", name="TaskSpan")
    assert taskSpanStart["traceGraphId"] == taskSpanEnd["traceGraphId"]


@pytest.mark.asyncio
async def test_createTask_wrapper_span_ends_with_error_on_exception() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    async def workFail() -> None:
        raise RuntimeError("Work failed")

    taskWork = tracer.createTask(
        workFail(),
        taskName="TaskFail",
        spanName="TaskFailSpan",
        domain="test",
    )
    
    with pytest.raises(RuntimeError):
        await taskWork
    
    records = await _collect(snapshot, queue)
    end = _find(records, recordType="spanEnd", name="TaskFailSpan")
    assert (end.get("attrs") or {}).get("status") == "error"
    assert (end.get("error") or {}).get("type") == "RuntimeError"


@pytest.mark.asyncio
async def test_events_outside_span_do_not_fragment_trace_graph_id() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    tracer.event("event.one").domain("test").emit()
    tracer.event("event.two").domain("test").emit()
    
    records = await _collect(snapshot, queue)
    events = [
        record for record in records
        if record.get("recordType") == "event"
        and record.get("name") in ("event.one", "event.two")
    ]
    assert len(events) == 2
    
    tgId1 = events[0]["traceGraphId"]
    tgId2 = events[1]["traceGraphId"]
    assert tgId1 == tgId2


@pytest.mark.asyncio
async def test_span_correlation_propagates_to_children() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("outer").domain("test").corrSpan(sessionId="session1").start()
    tracer.event("inside").domain("test").emit()
    scope.end()
    
    records = await _collect(snapshot, queue)
    inside = next(
        record for record in records
        if record.get("recordType") == "event"
        and record.get("name") == "inside"
    )
    assert inside["correlation"].get("sessionId") == "session1"
    assert inside.get("sessionId") == "session1" # promoted to top-level (promoteTopLevel=True)    


@pytest.mark.asyncio
async def test_event_correlation_is_record_local_and_does_not_update_ambient() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    scope = tracer.span("outer").domain("test").corrSpan(sessionId="session1").start()
    
    tracer.event("event.withOverlay").domain("test").corrEvent(llmProvider="openai").emit()
    tracer.event("event.after").domain("test").emit()

    scope.end()
    
    records = await _collect(snapshot, queue)

    withOverlay = next(
        record for record in records
        if record.get("recordType") == "event"
        and record.get("name") == "event.withOverlay"
    )
    after = next(
        record
        for record in records
        if record.get("recordType") == "event"
        and record.get("name") == "event.after"
    )
    assert withOverlay["correlation"].get("sessionId") == "session1"
    assert withOverlay["correlation"].get("llmProvider") == "openai"
    
    assert after["correlation"].get("sessionId") == "session1"
    assert after["correlation"].get("llmProvider") is None # Overlay must NOT leak into ambient


@pytest.mark.asyncio
async def test_nested_span_trace_graph_override_is_ignored() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    outer = tracer.span("outer").domain("test").start()
    outer_tg = outer.span.traceGraphId
    
    inner = tracer.startSpan("inner", domain="test", traceGraphId="tgOverrideShouldBeIgnored")
    inner_tg = inner.span.traceGraphId
    
    inner.end()
    outer.end()
    
    records = await _collect(snapshot, queue)
    
    assert inner_tg == outer_tg
    ignored = [
        record for record in records
        if record.get("recordType") == "event"
        and record.get("name") == "trace.graphId.nestedSpanOverrideIgnored"
    ]
    assert len(ignored) >= 1
    
    
@pytest.mark.asyncio
async def test_cross_task_span_end_attempt_emits_warning_and_not_span_end() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("cross").domain("test").start()
    
    async def end_in_other_task() -> None:
        scope.end()
    
    await asyncio.create_task(end_in_other_task())
    
    records = await _collect(snapshot, queue)

    warning = [
        record for record in records
        if record.get("recordType") == "event"
        and record.get("name") == "trace.span.crossTaskAttemptedEnd"
    ]
    assert len(warning) == 1
    
    spanEnd = [
        record for record in records
        if record.get("recordType") == "spanEnd"
        and record.get("name") == "cross"
    ]
    assert len(spanEnd) == 0

@pytest.mark.asyncio
async def test_kernel_root_span_singleton_and_cross_task_end_rejected() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    root1 = tracer.startKernelRootSpan(traceGraphId="tgFixed")
    root2 = tracer.startKernelRootSpan(traceGraphId="tgOther")
    assert root1 is root2 # Same scope returned
    assert tracer.getKernelRootSpanScope() is root1
    
    async def end_in_other_task() -> None:
        tracer.endKernelRootSpan(status="ok")
    
    await asyncio.create_task(end_in_other_task())
    
    records = await _collect(snapshot, queue)
        
    rejected = [
        record for record in records
        if record.get("recordType") == "event"
        and record.get("name") == "trace.kernelRoot.crossTaskAttemptedEnd"
    ]
    assert len(rejected) == 1
    
    # Proper end in owning task
    tracer.endKernelRootSpan(status="ok")
    records = await _collect([], queue)
    assert tracer.getKernelRootSpanScope() is None
    
    kernelEnd = [
        record for record in records
        if record.get("recordType") == "event"
        and record.get("name") == "kernelRun.end"
    ]
    assert len(kernelEnd) == 1


@pytest.mark.asyncio
async def test_span_start_eventCorrelation_is_record_local_and_not_in_span_snapshot() -> None:
    """
    eventCorrelation on startSpan should affect only the spanStart record,
    but NOT become part of span.correlation snapshot (span.correlation == baseCorrelation only).
    """
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    scope = tracer.startSpan(
        "outer",
        domain="test",
        spanCorrelation={"sessionId": "session1"},
        eventCorrelation={"llmProvider": "openai"},
    )
    
    # Emit something inside the span to observe ambient behaviour
    tracer.event("inside").domain("test").emit()
    scope.end()
    
    records = await _collect(snapshot, queue)
    
    spanStart = next(
        record for record in records
        if record["recordType"] == "spanStart"
        and record.get("name") == "outer"
    )
    inside = next(
        record for record in records
        if record["recordType"] == "event"
        and record.get("name") == "inside"
    )

    # spanStart record should include the overlay
    assert spanStart["correlation"]["sessionId"] == "session1"
    assert spanStart["correlation"]["llmProvider"] == "openai"

    # But span snapshot should not include eventCorrelation
    assert scope.span.correlation.get("sessionId") == "session1"
    assert scope.span.correlation.get("llmProvider") is None
    
    # And ambient inside the span should not see llmProvider
    assert inside["correlation"].get("sessionId") == "session1"
    assert inside["correlation"].get("llmProvider") is None


@pytest.mark.asyncio
async def test_scope_mismatch_warning_emitted_for_wrong_scope_key_usage() -> None:
    """
    If you use an event-scoped key in spanCorrelation (or span-scoped key in eventCorrelation),
    tracer should emit trace.correlation.scopeMismatch.
    """
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    scope = tracer.startSpan("outer", domain="test", spanCorrelation={"llmProvider": "openai"})
    scope.end()
    
    records = await _collect(snapshot, queue)
    warnings = [
        record for record in records
        if record["recordType"] == "event"
        and record.get("name") == "trace.correlation.scopeMismatch"
    ]
    assert len(warnings) >= 1
    
    # Optional: make sure mismatch payload mentions the key
    anyMentions = False
    for warning in warnings:
        mismatches = (warning.get("attrs") or {}).get("mismatches") or []
        if any(
            mismatch.get("key") == "llmProvider"
            for mismatch in mismatches
            if isinstance(mismatch, dict)
        ):
            anyMentions = True
            break
    assert anyMentions


@pytest.mark.asyncio
async def test_correlation_promotion_respects_promoteTopLevel_flag() -> None:
    """
    sessionId is promoteTopLevel=True, llmProvider is promoteTopLevel=False in _makeTracer()
    sessionId should appear as top-level field, llmProvider should NOT
    """
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    scope = tracer.startSpan("outer", domain="test", spanCorrelation={"sessionId": "session1"})
    tracer.event("event1").domain("test").corrEvent(llmProvider="openai").emit()
    scope.end()
    
    records = await _collect(snapshot, queue)
    event1 = next(
        record for record in records
        if record["recordType"] == "event"
        and record.get("name") == "event1"
    )
    
    # Promoted
    assert event1.get("sessionId") == "session1"
    # Not promoted
    assert "llmProvider" not in event1
    # But still present in correlation dict
    assert event1["correlation"].get("llmProvider") == "openai"


@pytest.mark.asyncio
async def test_span_end_restores_ambient_context() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    tracer.setTraceGraphId("tg_outer")
    tracer.updateCorrelation({"sessionId": "session0"}) # Ambient baseline
    
    scope = tracer.span("outer").domain("test").corrSpan(sessionId="session1").start()
    tracer.event("inside").domain("test").emit()
    scope.end()
    
    # After end, should be back to ambient baseline
    tracer.event("after").domain("test").emit()
    
    records = await _collect(snapshot, queue)
    
    inside = _find(records, recordType="event", name="inside")
    after = _find(records, recordType="event", name="after")
    
    assert inside["correlation"]["sessionId"] == "session1"
    assert after["correlation"]["sessionId"] == "session0"
    
    assert inside["traceGraphId"] == scope.span.traceGraphId
    assert after["traceGraphId"] == "tg_outer"
    
    # parentRecordId should be spanStart inside, and revert after
    spanStart = _find(records, recordType="spanStart", name="outer")
    assert inside["parentRecordId"] == spanStart["traceRecordId"]
    assert after["parentRecordId"] is None


@pytest.mark.asyncio
async def test_nested_span_end_restores_outer_span_context() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    outer = tracer.span("outer").domain("test").corrSpan(sessionId="s_outer").start()
    tracer.event("evt.outer.before").domain("test").emit()
    
    inner = tracer.span("inner").domain("test").corrSpan(sessionId="s_inner").start()
    tracer.event("evt.inner").domain("test").emit()
    inner.end()
    
    tracer.event("evt.outer.after").domain("test").emit()
    outer.end()
    
    records = await _collect(snapshot, queue)
    outerAfter = _find(records, recordType="event", name="evt.outer.after")
    assert outerAfter["correlation"]["sessionId"] == "s_outer"


@pytest.mark.asyncio
async def test_traceHub_stores_independent_record_copies() -> None:
    tracer = _makeTracer()
    snapshot1, queue1 = tracer.hub.subscribe()
    tracer.event("evt.mut").domain("test").corrEvent(llmProvider="openai").emit()
    await _flushLoop()
    
    delivered = await _drain(queue1)
    assert len(delivered) == 1
    record = delivered[0]
    
    # Mutate delivered record
    record["correlation"]["llmProvider"] = "MUTATED"
    record["tags"].append("MUTATED_TAG")
    record["attrs"]["x"] = 1
    
    # New subscriber should see original frozen record, not the mutated one
    snapshot2, queue2 = tracer.hub.subscribe()
    await _flushLoop()
    records2 = await _collect(snapshot2, queue2)
    
    original = _find(records2, recordType="event", name="evt.mut")
    assert original["correlation"]["llmProvider"] == "openai"
    assert "MUTATED_TAG" not in original["tags"]
    assert "x" not in (original.get("attrs") or {})


@pytest.mark.asyncio
async def test_traceHub_ring_buffer_capacity_drops_oldest() -> None:
    hub = TraceHub(capacity=5)
    tracer = Tracer(hub, _makeTracer().registry) # Reuse registry

    for ii in range(10):
        tracer.event(f"evt.{ii}").domain("test").emit()
    
    # Subscribe gets snapshot of last 5
    snapshot, queue = tracer.hub.subscribe()
    await _flushLoop()
    names = [record["name"] for record in snapshot]
    assert names == [f"evt.{ii}" for ii in range(5, 10)]
    hub.unsubscribe(queue)


@pytest.mark.asyncio
async def test_seq_monotonic_increases() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()
    
    scope = tracer.span("outer").domain("test").start()
    tracer.event("event1").domain("test").emit()
    tracer.event("event2").domain("test").emit()
    scope.end()

    records = await _collect(snapshot, queue)
    seqs = [record["seq"] for record in records]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))


@pytest.mark.asyncio
async def test_captureSource_populates_source_dict() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    tracer.event("evt").domain("test").captureSource(True).emit()
    records = await _collect(snapshot, queue)
    
    record = _find(records, recordType="event", name="evt")
    src = record.get("source")
    assert isinstance(src, dict)
    assert "file" in src and "line" in src and "function" in src and "module" in src
    assert isinstance(src["line"], int)


@pytest.mark.asyncio
async def test_createTask_wrapper_span_ends_on_cancellation() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    started = asyncio.Event()
    
    async def work() -> None:
        started.set()
        await asyncio.sleep(10)
    
    task = tracer.createTask(
        work(),
        taskName="CancelMe",
        spanName="CancelSpan",
        domain="test",
    )
    
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    
    records = await _collect(snapshot, queue, extraFlushes=2)
    end = _find(records, recordType="spanEnd", name="CancelSpan")
    assert (end.get("attrs") or {}).get("status") == "cancelled"
    assert (end.get("attrs") or {}).get("cancelCategory") == "asyncio"
    assert end.get("error") is None


@pytest.mark.asyncio
async def test_event_explicit_span_and_explicit_parentRecordId_both_respected() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    outer = tracer.span("outer").domain("test").start()
    tracer.traceEvent(
        "evt",
        domain="test",
        span=outer.span,
        parentRecordId="tr_explicitParent",
    )
    outer.end()
    
    records = await _collect(snapshot, queue)
    event = _find(records, recordType="event", name="evt")
    assert event["spanId"] == outer.span.spanId
    assert event["parentRecordId"] == "tr_explicitParent"


@pytest.mark.asyncio
async def test_startSpan_eventCorrelation_warns_for_span_scoped_key() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("outer").domain("test").corrEvent(sessionId="session1").start()
    scope.end()
    
    records = await _collect(snapshot, queue)
    warnings = _filter(records, recordType="event", name="trace.correlation.scopeMismatch")
    assert len(warnings) >= 1


@pytest.mark.asyncio
async def test_internalEmitWarning_is_guarded_and_does_not_recurse() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    # Force a warning: use bad scope key
    tracer.event("evt.badScope").domain("test").corrEvent(sessionId="session1").emit()
    
    records = await _collect(snapshot, queue)
    warnings = _filter(records, recordType="event", name="trace.correlation.scopeMismatch")
    # Should exist, and should not chain endlessly into more tracer warnings
    assert 1 <= len(warnings) < 10

@pytest.mark.asyncio
async def test_cross_task_span_end_leaves_original_task_context_unchanged() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("cross").domain("test").start()
    spanId = scope.span.spanId
    
    async def end_elsewhere() -> None:
        scope.end()
    
    await asyncio.create_task(end_elsewhere())
    
    # Still in original task: emit and see whether it still attaches to the span
    tracer.event("evt.afterCrossEnd").domain("test").emit()
    
    records = await _collect(snapshot, queue, extraFlushes=2)
    event = _find(records, recordType="event", name="evt.afterCrossEnd")
    assert event["spanId"] == spanId


@pytest.mark.asyncio
async def test_explicit_span_uses_that_span_graph_and_parentSpan_even_inside_other_span() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    outer = tracer.span("outer").domain("test").start()
    inner = tracer.span("inner").domain("test").start()
    
    tracer.event("evt.explicitOuter").domain("test").span(outer.span).emit()
    
    inner.end()
    outer.end()
    
    records = await _collect(snapshot, queue)
    event = _find(records, recordType="event", name="evt.explicitOuter")
    assert event["spanId"] == outer.span.spanId
    assert event["parentSpanId"] == outer.span.parentSpanId
    assert event["traceGraphId"] == outer.span.traceGraphId


@pytest.mark.asyncio
async def test_explicit_span_defaults_parentRecordId_and_correlation_to_that_span() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    outer = tracer.span("outer").domain("test").corrSpan(sessionId="outerSession").start()
    inner = tracer.span("inner").domain("test").corrSpan(sessionId="innerSession").start()

    tracer.event("evt.explicitOuter").domain("test").span(outer.span).emit()

    inner.end()
    outer.end()

    records = await _collect(snapshot, queue)
    event = _find(records, recordType="event", name="evt.explicitOuter")
    outerStart = _find(records, recordType="spanStart", name="outer")
    
    assert event["spanId"] == outer.span.spanId
    assert event["parentRecordId"] == outerStart["traceRecordId"]
    assert event["correlation"]["sessionId"] == "outerSession"


@pytest.mark.asyncio
async def test_cross_task_end_attempt_does_not_prevent_owner_from_later_ending_span() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("cross").domain("test").start()

    async def end_elsewhere() -> None:
        scope.end()

    await asyncio.create_task(end_elsewhere())
    
    # Owner task should still be able to close it properly
    scope.end()
    
    records = await _collect(snapshot, queue)
    ends = _filter(records, recordType="spanEnd", name="cross")
    assert len(ends) == 1


@pytest.mark.asyncio
async def test_span_end_with_timeout_outcome_sets_timeout_status_and_timeoutMs() -> None:
    from backend.core.tracing import TraceTimeoutOutcome
    
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("outer").domain("test").start()
    scope.end(
        TraceTimeoutOutcome(
            reason="RequestTimedOut",
            message="Request timed out",
            timeoutMs=30000,
        ),
        domain="test",
    )
    
    records = await _collect(snapshot, queue)
    end = _find(records, recordType="spanEnd", name="outer")
    assert (end.get("attrs") or {}).get("status") == "timeout"
    assert (end.get("attrs") or {}).get("timeoutMs") == 30000
    assert end.get("reason") == "RequestTimedOut"


@pytest.mark.asyncio
async def test_span_end_with_policy_refused_outcome_sets_policy_fields() -> None:
    from backend.core.tracing import TracePolicyRefusedOutcome
    
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("outer").domain("test").start()
    scope.end(
        TracePolicyRefusedOutcome(
            reason="TestPolicyRefused",
            policyId="policy.test",
            refusalCategory="TestCategory",
        ),
        domain="test",
    )
    
    records = await _collect(snapshot, queue)
    end = _find(records, recordType="spanEnd", name="outer")
    assert (end.get("attrs") or {}).get("status") == "policyRefused"
    assert end.get("reason") == "TestPolicyRefused"
    assert (end.get("attrs") or {}).get("policyId") == "policy.test"
    assert (end.get("attrs") or {}).get("refusalCategory") == "TestCategory"


@pytest.mark.asyncio
async def test_span_end_with_debugger_intervened_outcome_sets_debugger_fields() -> None:
    from backend.core.tracing import TraceDebuggerIntervenedOutcome
    
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("outer").domain("test").start()
    scope.end(
        TraceDebuggerIntervenedOutcome(
            reason="TestDebuggerReason",
            debuggerAction="TestAction",
            debuggerClientId="dbg1",
            requestId="req1",
        ),
        domain="test",
    )
    
    records = await _collect(snapshot, queue)
    end = _find(records, recordType="spanEnd", name="outer")
    assert (end.get("attrs") or {}).get("status") == "debuggerIntervened"
    assert (end.get("attrs") or {}).get("debuggerAction") == "TestAction"
    assert (end.get("attrs") or {}).get("debuggerClientId") == "dbg1"
    assert (end.get("attrs") or {}).get("requestId") == "req1"


@pytest.mark.asyncio
async def test_scope_ok_helper_emits_ok_span_end() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("outer").domain("test").start()
    scope.ok(reason="FinishedNormally", message="done", domain="test")
    
    records = await _collect(snapshot, queue)
    end = _find(records, recordType="spanEnd", name="outer")
    assert end.get("reason") == "FinishedNormally"
    assert end.get("message") == "done"
    assert (end.get("attrs") or {}).get("status") == "ok"
    assert end.get("error") is None


@pytest.mark.asyncio
async def test_scope_fail_helper_emits_error_span_end() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("outer").domain("test").start()
    scope.fail(reason="Boom", message="failed", domain="test")
    
    records = await _collect(snapshot, queue)
    end = _find(records, recordType="spanEnd", name="outer")
    assert end.get("reason") == "Boom"
    assert end.get("message") == "failed"
    assert (end.get("attrs") or {}).get("status") == "error"


@pytest.mark.asyncio
async def test_scope_error_helper_emits_error_span_end() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("outer").domain("test").start()
    scope.error(reason="Boom", message="failed", domain="test")
    
    records = await _collect(snapshot, queue)
    end = _find(records, recordType="spanEnd", name="outer")
    assert end.get("reason") == "Boom"
    assert end.get("message") == "failed"
    assert (end.get("attrs") or {}).get("status") == "error"


@pytest.mark.asyncio
async def test_scope_cancel_helper_emits_cancelled_span_end() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("outer").domain("test").start()
    scope.cancel(reason="UserCancelled", message="user cancelled it", cancelCategory="user", domain="test")
    
    records = await _collect(snapshot, queue)
    end = _find(records, recordType="spanEnd", name="outer")
    assert end.get("reason") == "UserCancelled"
    assert end.get("message") == "user cancelled it"
    assert (end.get("attrs") or {}).get("status") == "cancelled"
    assert (end.get("attrs") or {}).get("cancelCategory") == "user"
    assert end.get("error") is None


@pytest.mark.asyncio
async def test_scope_timeout_helper_emits_timout_span_end() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("outer").domain("test").start()
    scope.timeout(reason="RequestTimedOut", message="timed out", timeoutMs=1234, domain="test")
    
    records = await _collect(snapshot, queue)
    end = _find(records, recordType="spanEnd", name="outer")
    assert end.get("reason") == "RequestTimedOut"
    assert end.get("message") == "timed out"
    assert (end.get("attrs") or {}).get("status") == "timeout"
    assert (end.get("attrs") or {}).get("timeoutMs") == 1234


@pytest.mark.asyncio
async def test_scope_policyRefused_helper_emits_policy_refused_span_end() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("outer").domain("test").start()
    scope.policyRefused(
        reason="DeniedByPolicy",
        message="refused",
        policyId="policy.test",
        refusalCategory="Denied",
        domain="test"
    )
    
    records = await _collect(snapshot, queue)
    end = _find(records, recordType="spanEnd", name="outer")
    assert end.get("reason") == "DeniedByPolicy"
    assert end.get("message") == "refused"
    assert (end.get("attrs") or {}).get("status") == "policyRefused"
    assert (end.get("attrs") or {}).get("policyId") == "policy.test"
    assert (end.get("attrs") or {}).get("refusalCategory") == "Denied"


@pytest.mark.asyncio
async def test_scope_debuggerIntervened_helper_emits_debugger_intervened_span_end() -> None:
    tracer = _makeTracer()
    snapshot, queue = tracer.hub.subscribe()

    scope = tracer.span("outer").domain("test").start()
    scope.debuggerIntervened(
        reason="DebuggerStoppedExecution",
        message="stopped by debugger",
        debuggerAction="policy.test",
        debuggerClientId="dbg1",
        requestId="req1",
        domain="test"
    )
    
    records = await _collect(snapshot, queue)
    end = _find(records, recordType="spanEnd", name="outer")
    assert end.get("reason") == "DebuggerStoppedExecution"
    assert end.get("message") == "stopped by debugger"
    assert (end.get("attrs") or {}).get("status") == "debuggerIntervened"
    assert (end.get("attrs") or {}).get("debuggerAction") == "policy.test"
    assert (end.get("attrs") or {}).get("debuggerClientId") == "dbg1"
    assert (end.get("attrs") or {}).get("requestId") == "req1"
