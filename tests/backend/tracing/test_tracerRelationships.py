# file: tests/backend/tracing/test_tracerRelationships.py ; version: 4
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
from tests.backend.tracing.helpers import CollectingDestination, checkpointDestination, createSinkTracer, recordsAfter


def testParentlessEventUsesTracerOrigin() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))

    tracer.event().message("started").emit()

    record = collector.records[-1]
    assert record.origin == "actant.test"
    assert record.kind == "event"
    assert record.spanId is None
    assert record.spanStartEventId is None


def testNestedRelationshipsAndSpanStartRegistrationAreExplicit() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    checkpoint = checkpointDestination(collector)
    pipelineType = TraceSpanType(name="pipeline.run", domain="pipeline")
    stageType = TraceSpanType(name="pipeline.stage", domain="pipeline")

    pipeline = tracer.span(pipelineType).start()
    eventId = tracer.event().message("inside pipeline").emit()
    stage = tracer.span(stageType).start()
    stageEventId = tracer.event().emit()
    stageEndId = stage.complete()
    pipelineEndId = pipeline.complete()

    pipelineStart, event, stageStart, stageEvent, stageEnd, pipelineEnd = recordsAfter(
        collector,
        checkpoint,
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
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    span = tracer.span().start()
    spanContext = span.getContext()
    span.complete()

    tracer.event().span(spanContext).message("late").emit()

    lateRecord = collector.records[-1]
    assert lateRecord.kind == "event"
    assert lateRecord.spanId == spanContext.spanId
    assert lateRecord.spanStartEventId == spanContext.spanStartEventId


def testDefaultCustomOutcomeUsesDefaultDefinitionWithoutRegistration() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    initialDefinitions = tracer.getTraceTypeDefinitions()
    span = tracer.span().start()

    # A custom outcome on the default trace span type does not create a new
    # trace type definition; it remains a record-local outcome override.
    span.end("superseded")

    endRecord = collector.records[-1]
    assert endRecord.outcome == "superseded"
    assert endRecord.type == "trace.span.superseded"
    assert (
        endRecord.traceTypeDefinitionId
        == TRACE_SPAN.getDefinition().traceTypeDefinitionId
    )
    assert tracer.getTraceTypeDefinitions() == initialDefinitions


def testExplicitSpanTypeRejectsUnknownOutcome() -> None:
    tracer = createSinkTracer()
    explicitSpan = tracer.span(TraceSpanType(name="pipeline.run")).start()

    with pytest.raises(TraceUnknownOutcomeError):
        explicitSpan.end("superseded")  # explicitSpan doesn't have superseded outcome

    explicitSpan.complete()


def testExplicitSpanTypeUsesDeclaredCustomOutcome() -> None:
    collector = CollectingDestination()
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
    tracer = Tracer(origin="actant.test", destinations=(collector,))

    tracer.span(explicitTraceType).start().end("superseded")

    record = collector.records[-1]
    assert record.type == "pipeline.run.replaced"
    assert record.outcome == "superseded"
    assert record.level == "warning"


def testBuilderIsConsumedByEmitAttempt() -> None:
    tracer = createSinkTracer()
    builder = tracer.event(TraceEventType(name="pipeline.ready"))
    builder.emit()

    with pytest.raises(TraceBuilderConsumedError):
        builder.message("again")


def testSpanDurationMatchesMonotonicRecordTimestamps() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    checkpoint = checkpointDestination(collector)

    tracer.span().start().complete()

    startRecord, endRecord = recordsAfter(
        collector,
        checkpoint,
    )
    assert endRecord.durationNs == (
        endRecord.timestampMonotonicNs
        - startRecord.timestampMonotonicNs
    )


def testEventBuilderIsConsumedByFailedEmissionAttempt() -> None:
    tracer = createSinkTracer()
    builder = tracer.event().attr("unsupported", object())

    with pytest.raises(TypeError):
        builder.emit()

    with pytest.raises(TraceBuilderConsumedError):
        builder.emit()

    with pytest.raises(TraceBuilderConsumedError):
        builder.message("cannot reuse")


def testSpanBuilderIsConsumedByFailedStartAttempt() -> None:
    tracer = createSinkTracer()
    builder = tracer.span().attr("unsupported", object())

    with pytest.raises(TypeError):
        builder.start()

    with pytest.raises(TraceBuilderConsumedError):
        builder.start()

    assert tracer.getActiveSpanCount() == 0


def testBuilderCreatedBeforeCloseDoesNotReserveEmissionPermission() -> None:
    tracer = createSinkTracer()
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

    tracer = createSinkTracer()
    deceptiveValue = EqualToEmpty()

    with pytest.raises(TypeError):
        tracer.emitEvent(
            domain=deceptiveValue,  # ty: ignore[invalid-argument-type]
        )

    with pytest.raises(TypeError):
        tracer.event().domain(
            deceptiveValue,  # ty: ignore[invalid-argument-type]
        )
