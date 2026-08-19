# file: tests/backend/tracing/test_semanticCompleteness.py ; version: 3
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest

from backend.core.ids import Uuid7Id
from backend.tracing import (
    TRACE_EVENT,
    TRACE_SPAN,
    TraceContextError,
    TraceEventId,
    TraceEventType,
    TraceExplicitTypeOverrideError,
    TraceGeneratedType,
    TraceProducerId,
    Tracer,
    TraceRecord,
    TraceReference,
    TraceSinkDestination,
    TraceSpanId,
    TraceSpanStateError,
    TraceSpanType,
    canonicalJson,
)
from tests.backend.tracing.helpers import CollectingDestination, checkpointDestination, createSinkTracer, recordsAfter


@dataclass(frozen=True, slots=True)
class JobId(Uuid7Id): # TODO: replace when real class exists
    pass


def testCanonicalJsonUsesPortableStableRepresentation() -> None:
    value = {
        "z": [True, None, -3],
        "a": 'line\nquote"slash\\snowman☃',
    }

    text = canonicalJson(value)

    assert text == (
        '{"a":"line\\nquote\\"slash\\\\snowman☃",'
        '"z":[true,null,-3]}'
    )
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    definitionId = TraceEventType(
        name="portable.event",
    ).getDefinition().traceTypeDefinitionId
    assert str(definitionId).startswith("sha256:")
    assert len(expected) == 64  # noqa: PLR2004


@pytest.mark.parametrize(
    "value",
    [
        1.5,
        b"bytes",
        object(),
    ],
)
def testCanonicalJsonRejectsUnsupportedPortableValues(value: object) -> None:
    with pytest.raises(TypeError):
        canonicalJson(value)


def testCanonicalJsonRejectsLoneSurrogate() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        canonicalJson("\ud800")


def testSpanDefinitionRejectsDuplicateGeneratedLabels() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        TraceSpanType(
            name="pipeline.run",
            failed=TraceGeneratedType("completed", "warning"),
        )


def testParentlessEvidenceRequiresOrigin() -> None:
    sink = TraceSinkDestination()
    tracer = Tracer(destinations=(sink,))

    with pytest.raises(TraceContextError):
        tracer.event().emit()

    with pytest.raises(TraceContextError):
        tracer.span().start()


def testExplicitSpanAndOriginConflictIsRejectedByDirectAndFluentApis() -> None:
    tracer = createSinkTracer()
    span = tracer.span().start()
    context = span.getContext()

    with pytest.raises(TraceContextError):
        tracer.emitEvent(span=context, origin="actant.other")

    with pytest.raises(TraceContextError):
        tracer.event().span(context).origin("actant.other")

    with pytest.raises(TraceContextError):
        tracer.event().origin("actant.other").span(context)

    span.complete()


def testDefaultCustomOutcomeOverridesAreRecordLocal() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    definitionsBefore = tracer.getTraceTypeDefinitions()
    span = tracer.span().start()

    span.end("custom", label="discarded", level="warning")

    record = collector.records[-1]
    assert record.outcome == "custom"
    assert record.type == "trace.span.discarded"
    assert record.level == "warning"
    assert (
        record.traceTypeDefinitionId
        == TRACE_SPAN.getDefinition().traceTypeDefinitionId
    )
    assert tracer.getTraceTypeDefinitions() == definitionsBefore


def testDeclaredSpanTypeRejectsRecordLocalLabelOverride() -> None:
    tracer = createSinkTracer()
    span = tracer.span(TraceSpanType(name="pipeline.run")).start()

    with pytest.raises(TraceExplicitTypeOverrideError):
        span.complete(label="done")

    span.complete()


def testLaterBuilderAttributesReplaceEarlierValues() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))

    (
        tracer.event()
        .attr("attempt", 1)
        .attrs({"attempt": 2, "delay": 0.5})
        .attr("attempt", 3)
        .emit()
    )

    assert collector.records[-1].attributes == {
        "attempt": 3,
        "delay": 0.5,
    }


def testDirectAndFluentEventApisProduceEquivalentSemantics() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    checkpoint = checkpointDestination(collector)

    eventType = TraceEventType(
        name="pipeline.retry-scheduled",
        domain="pipeline",
        event=TraceGeneratedType(
            "pipeline.retry-scheduled",
            "warning",
            "Retry scheduled",
        ),
    )
    cause = TraceReference(kind="job", id="job-1")

    (
        tracer.event(eventType)
        .message("retrying")
        .attr("attempt", 2)
        .causedBy(cause)
        .emit()
    )
    tracer.emitEvent(
        traceType=eventType,
        message="retrying",
        attributes={"attempt": 2},
        causedBy=(cause,),
    )

    first, second = recordsAfter(collector, checkpoint)
    comparedFields = (
        "kind",
        "domain",
        "type",
        "traceTypeDefinitionId",
        "level",
        "message",
        "attributes",
        "exceptionSnapshot",
        "spanId",
        "spanStartEventId",
        "parentSpanId",
        "spanFamily",
        "origin",
        "outcome",
        "durationNs",
        "causedBy",
        "actantRunId",
        "applicationId",
        "applicationRunId",
    )
    for fieldName in comparedFields:
        assert getattr(first, fieldName) == getattr(second, fieldName)


def testTraceReferencesAcceptTypedIdsAndDeduplicate() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    jobId = JobId.new()
    eventId = TraceEventId.new()

    tracer.emitEvent(
        causedBy=(
            ("job", jobId),
            ("job", jobId),
            TraceReference.fromIdentity("trace.event", eventId),
        ),
    )

    assert collector.records[-1].causedBy == (
        TraceReference(kind="job", id=str(jobId)),
        TraceReference(kind="trace.event", id=str(eventId)),
    )


def testRejectedEndAttributesLeaveSpanActiveAndDoNotConsumeSequence() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    checkpoint = checkpointDestination(collector)

    span = tracer.span().start()

    with pytest.raises(TypeError):
        span.complete(attributes={"bad": object()})

    assert tracer.getActiveSpanCount() == 1
    span.complete()

    spanStart, spanEnd = recordsAfter(collector, checkpoint)

    assert spanEnd.sequence == spanStart.sequence + 1
    assert [spanStart.kind, spanEnd.kind] == [
        "spanStart",
        "spanEnd",
    ]


def testParentSpanCannotEndWhileNestedChildIsAmbient() -> None:
    tracer = createSinkTracer()
    parent = tracer.span().start()
    child = tracer.span().start()

    with pytest.raises(TraceSpanStateError):
        parent.complete()

    child.complete()
    parent.complete()


def testImportedSpanEndMayOmitDuration() -> None:
    startId = TraceEventId.new()
    record = TraceRecord(
        eventId=TraceEventId.new(),
        traceProducerId=TraceProducerId.new(),
        sequence=1,
        timestampUnixNs=1,
        timestampMonotonicNs=1,
        kind="spanEnd",
        domain="",
        type="trace.span.completed",
        traceTypeDefinitionId=(
            TRACE_SPAN.getDefinition().traceTypeDefinitionId
        ),
        level="info",
        spanId=TraceSpanId.new(),
        spanStartEventId=startId,
        spanFamily="trace.span",
        outcome="completed",
        durationNs=None,
    )

    assert record.durationNs is None


def testDefaultEventLabelOverrideIsRecordLocal() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    definitionsBefore = tracer.getTraceTypeDefinitions()

    tracer.event().label("creator.notice").emit()

    record = collector.records[-1]
    assert record.type == "creator.notice"
    assert (
        record.traceTypeDefinitionId
        == TRACE_EVENT.getDefinition().traceTypeDefinitionId
    )
    assert tracer.getTraceTypeDefinitions() == definitionsBefore


def testDefaultSpanStartLabelOverrideIsRecordLocal() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    definitionsBefore = tracer.getTraceTypeDefinitions()

    span = tracer.span().label("opened").start()
    span.complete()

    spanOpened = [
        record
        for record in collector.records
        if record.type == "trace.span.opened"
    ]
    assert len(spanOpened) == 1
    assert (
        spanOpened[0].traceTypeDefinitionId
        == TRACE_SPAN.getDefinition().traceTypeDefinitionId
    )
    assert tracer.getTraceTypeDefinitions() == definitionsBefore


def testDeclaredEventTypeRejectsRecordLocalLabelOverride() -> None:
    tracer = createSinkTracer()

    with pytest.raises(TraceExplicitTypeOverrideError):
        tracer.event(TraceEventType(name="pipeline.ready")).label(
            "pipeline.other",
        ).emit()
