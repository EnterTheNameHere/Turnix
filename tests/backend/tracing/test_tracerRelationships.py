# file: tests/backend/tracing/test_tracerRelationships.py ; version: 3
from __future__ import annotations

import pytest

from backend.tracing import (
    TRACE_SPAN,
    TraceBuilderConsumedError,
    TraceClosedError,
    TraceEventType,
    TraceGeneratedType,
    Tracer,
    TraceSpanType,
    TraceUnknownOutcomeError,
)
from tests.backend.tracing.helpers import CollectingDestination


def testParentLessEventUsesTracerOrigin() -> None:
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))

    tracer.event().message("started").emit()

    record = destination.records[-1]
    assert record.origin == "actant.test"
    assert record.kind == "event"
    assert record.spanId is None
    assert record.spanStartEventId is None


def testNestedRelationshipsAndSpanStartRegistrationAreExplicit() -> None:
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))
    pipelineType = TraceSpanType(name="pipeline.run", domain="pipeline")
    stageType = TraceSpanType(name="pipeline.stage", domain="pipeline")

    pipeline = tracer.span(pipelineType).start()
    eventId = tracer.event().message("inside pipeline").emit()
    stage = tracer.span(stageType).start()
    stageEventId = tracer.event().emit()
    stageEndId = stage.complete()
    pipelineEndId = pipeline.complete()

    pipelineStart, event, stageStart, stageEvent, stageEnd, pipelineEnd = (
        destination.records
    )

    assert pipelineStart.kind == "spanStart"
    assert pipelineStart.parentSpanId is None
    assert pipelineStart.origin == "actant.test"
    assert pipelineStart.spanStartEventId == pipelineStart.eventId

    assert event.eventId == eventId
    assert event.spanId == pipelineStart.spanId
    assert event.spanStartEventId == pipelineStart.eventId
    assert event.spanFamily is None

    assert stageStart.parentSpanId == pipelineStart.spanId
    assert stageStart.origin is None
    assert stageStart.spanStartEventId == stageStart.eventId

    assert stageEvent.eventId == stageEventId
    assert stageEvent.spanId == stageStart.spanId
    assert stageEvent.spanStartEventId == stageStart.eventId

    assert stageEnd.eventId == stageEndId
    assert stageEnd.spanId == stageStart.spanId
    assert stageEnd.spanStartEventId == stageStart.eventId
    assert stageEnd.spanFamily == "pipeline.stage"

    assert pipelineEnd.eventId == pipelineEndId
    assert pipelineEnd.spanId == pipelineStart.spanId
    assert pipelineEnd.spanStartEventId == pipelineStart.eventId


def testLateEventCanReferenceEndedSpanThroughTransferredContext() -> None:
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))
    span = tracer.span().start()
    spanContext = span.getContext()
    span.complete()

    tracer.event().span(spanContext).message("late").emit()

    lateRecord = destination.records[-1]
    assert lateRecord.kind == "event"
    assert lateRecord.spanId == spanContext.spanId
    assert lateRecord.spanStartEventId == spanContext.spanStartEventId


def testDefaultCustomOutcomeUsesDefaultDefinitionWithoutRegistration() -> None:
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))
    initialDefinitions = tracer.getTraceTypeDefinitions()
    span = tracer.span().start()

    # Custom outcome on default trace span type does not create trace type
    # definition, but allows use of unregistered custom outcome
    span.end("superseded")

    endRecord = destination.records[-1]
    assert endRecord.outcome == "superseded"
    assert endRecord.type == "trace.span.superseded"
    assert (
        endRecord.traceTypeDefinitionId
        == TRACE_SPAN.getDefinition().traceTypeDefinitionId
    )
    assert tracer.getTraceTypeDefinitions() == initialDefinitions


def testExplicitSpanTypeRejectsUnknownOutcome() -> None:
    tracer = Tracer(origin="actant.test")
    explicitSpan = tracer.span(TraceSpanType(name="pipeline.run")).start()

    with pytest.raises(TraceUnknownOutcomeError):
        explicitSpan.end("superseded") # explicitSpan doesn't have superseded outcome

    explicitSpan.complete()


def testExplicitSpanTypeUsesDeclaredCustomOutcome() -> None:
    destination = CollectingDestination()
    explicitTraceType = TraceSpanType(
        name="pipeline.run",
        customOutcomes={
            "superseded": TraceGeneratedType(
                "replaced",
                "warning",
                "Superseded",
            ),
        },
    )
    tracer = Tracer(origin="actant.test", destinations=(destination,))

    tracer.span(explicitTraceType).start().end("superseded")

    record = destination.records[-1]
    assert record.type == "pipeline.run.replaced"
    assert record.outcome == "superseded"
    assert record.level == "warning"


def testBuilderIsConsumedByEmitAttempt() -> None:
    tracer = Tracer(origin="actant.test")
    builder = tracer.event(TraceEventType(name="pipeline.ready"))
    builder.emit()

    with pytest.raises(TraceBuilderConsumedError):
        builder.message("again")


def testSpanDurationMatchesMonotonicRecordTimestamps() -> None:
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))

    tracer.span().start().complete()

    startRecord, endRecord = destination.records
    assert endRecord.durationNs == (
        endRecord.timestampMonotonicNs
        - startRecord.timestampMonotonicNs
    )


def testEventBuilderIsConsumedByFailedEmissionAttempt() -> None:
    tracer = Tracer(origin="actant.test")
    builder = tracer.event().attr("unsupported", object())

    with pytest.raises(TypeError):
        builder.emit()

    with pytest.raises(TraceBuilderConsumedError):
        builder.emit()

    with pytest.raises(TraceBuilderConsumedError):
        builder.message("cannot reuse")


def testSpanBuilderIsConsumedByFailedStartAttempt() -> None:
    tracer = Tracer(origin="actant.test")
    builder = tracer.span().attr("unsupported", object())

    with pytest.raises(TypeError):
        builder.start()

    with pytest.raises(TraceBuilderConsumedError):
        builder.start()

    assert tracer.getActiveSpanCount() == 0


def testBuilderCreatedBeforeCloseDoesNotReserveEmissionPermission() -> None:
    tracer = Tracer(origin="actant.test")
    builder = tracer.event()

    tracer.close()

    with pytest.raises(TraceClosedError):
        builder.emit()

    with pytest.raises(TraceBuilderConsumedError):
        builder.emit()


def testDomainOverrideValidatesStringBeforeTestingForEmptyDomain() -> None:
    class EqualToEmpty:  # noqa: PLW1641
        def __eq__(self, other: object) -> bool:
            return other == ""

    tracer = Tracer(origin="actant.test")
    deceptiveValue = EqualToEmpty()

    with pytest.raises(TypeError):
        tracer.emitEvent(
            domain=deceptiveValue, # ty: ignore[invalid-argument-type]
        )

    with pytest.raises(TypeError):
        tracer.event().domain(
            deceptiveValue, # ty: ignore[invalid-argument-type]
        )
