# file: tests/backend/tracing/test_semanticCompleteness.py ; version: 2
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
    TraceSpanId,
    TraceSpanStateError,
    TraceSpanType,
    canonicalJson,
)
from tests.backend.tracing.helpers import CollectingDestination


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
    assert len(expected) == 64


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
    with pytest.raises(ValueError):
        canonicalJson("\ud800")


def testSpanDefinitionRejectsDuplicateGeneratedLabels() -> None:
    with pytest.raises(ValueError):
        TraceSpanType(
            name="pipeline.run",
            failed=TraceGeneratedType("completed", "warning"),
        )


def testParentlessEvidenceRequiresOrigin() -> None:
    tracer = Tracer()

    with pytest.raises(TraceContextError):
        tracer.event().emit()

    with pytest.raises(TraceContextError):
        tracer.span().start()


def testExplicitSpanAndOriginConflictIsRejectedByDirectAndFluentApis() -> None:
    tracer = Tracer(origin="actant.test")
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
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))
    definitionsBefore = tracer.getTraceTypeDefinitions()
    span = tracer.span().start()

    span.end("custom", label="discarded", level="warning")

    record = destination.records[-1]
    assert record.outcome == "custom"
    assert record.type == "trace.span.discarded"
    assert record.level == "warning"
    assert (
        record.traceTypeDefinitionId
        == TRACE_SPAN.getDefinition().traceTypeDefinitionId
    )
    assert tracer.getTraceTypeDefinitions() == definitionsBefore


def testDeclaredSpanTypeRejectsRecordLocalLabelOverride() -> None:
    tracer = Tracer(origin="actant.test")
    span = tracer.span(TraceSpanType(name="pipeline.run")).start()

    with pytest.raises(TraceExplicitTypeOverrideError):
        span.complete(label="done")

    span.complete()


def testLaterBuilderAttributesReplaceEarlierValues() -> None:
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))

    (
        tracer.event()
        .attr("attempt", 1)
        .attrs({"attempt": 2, "delay": 0.5})
        .attr("attempt", 3)
        .emit()
    )

    assert destination.records[-1].attributes == {
        "attempt": 3,
        "delay": 0.5,
    }


def testDirectAndFluentEventApisProduceEquivalentSemantics() -> None:
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))
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

    first, second = destination.records
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
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))
    jobId = JobId.new()
    eventId = TraceEventId.new()

    tracer.emitEvent(
        causedBy=(
            ("job", jobId),
            ("job", jobId),
            TraceReference.fromIdentity("trace.event", eventId),
        ),
    )

    assert destination.records[-1].causedBy == (
        TraceReference(kind="job", id=str(jobId)),
        TraceReference(kind="trace.event", id=str(eventId)),
    )


def testRejectedEndAttributesLeaveSpanActiveAndDoNotConsumeSequence() -> None:
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))
    span = tracer.span().start()

    with pytest.raises(TypeError):
        span.complete(attributes={"bad": object()})

    assert tracer.getActiveSpanCount() == 1
    span.complete()

    assert [record.sequence for record in destination.records] == [1, 2]
    assert [record.kind for record in destination.records] == [
        "spanStart",
        "spanEnd",
    ]


def testParentSpanCannotEndWhileNestedChildIsAmbient() -> None:
    tracer = Tracer(origin="actant.test")
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
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))
    definitionsBefore = tracer.getTraceTypeDefinitions()

    tracer.event().label("creator.notice").emit()

    record = destination.records[-1]
    assert record.type == "creator.notice"
    assert (
        record.traceTypeDefinitionId
        == TRACE_EVENT.getDefinition().traceTypeDefinitionId
    )
    assert tracer.getTraceTypeDefinitions() == definitionsBefore


def testDefaultSpanStartLabelOverrideIsRecordLocal() -> None:
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))
    definitionsBefore = tracer.getTraceTypeDefinitions()

    span = tracer.span().label("opened").start()
    span.complete()

    assert destination.records[0].type == "trace.span.opened"
    assert (
        destination.records[0].traceTypeDefinitionId
        == TRACE_SPAN.getDefinition().traceTypeDefinitionId
    )
    assert tracer.getTraceTypeDefinitions() == definitionsBefore


def testDeclaredEventTypeRejectsRecordLocalLabelOverride() -> None:
    tracer = Tracer(origin="actant.test")

    with pytest.raises(TraceExplicitTypeOverrideError):
        tracer.event(TraceEventType(name="pipeline.ready")).label(
            "pipeline.other",
        ).emit()
