# file: tests/backend/tracing/test_evidenceAudit.py ; version: 2
from __future__ import annotations

from backend.tracing import (
    TraceEventType,
    TraceGeneratedType,
    Tracer,
    TraceSpanType,
)
from tests.backend.tracing.helpers import CollectingDestination


def testPublishedEvidenceIsSelfConsistentAndDefinitionOrdered() -> None:
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))
    pipelineType = TraceSpanType(
        name="pipeline.run",
        domain="pipeline",
        failed=TraceGeneratedType("aborted", "warning", "Aborted"),
    )
    stageType = TraceSpanType(
        name="pipeline.stage",
        domain="pipeline",
    )
    decisionType = TraceEventType(
        name="pipeline.decision",
        domain="pipeline",
        event=TraceGeneratedType("pipeline.decision", "info", "Decision"),
    )

    pipeline = tracer.span(pipelineType).start()
    tracer.event(decisionType).attr("accepted", True).emit()
    stage = tracer.span(stageType).start()
    stageContext = stage.getContext()
    stage.complete()
    tracer.event(decisionType).span(stageContext).message("late").emit()
    pipeline.fail(message="no output")

    definitions = {
        definition.traceTypeDefinitionId: definition
        for definition in destination.definitions
    }
    startsBySpanId = {
        record.spanId: record
        for record in destination.records
        if record.kind == "spanStart"
    }

    assert [record.sequence for record in sorted(
        destination.records,
        key=lambda record: record.sequence,
    )] == list(range(1, len(destination.records) + 1))

    for record in destination.records:
        assert record.traceTypeDefinitionId in definitions
        definitionIndex = next(
            index
            for index, (operation, value) in enumerate(destination.operations)
            if (
                operation == "definition"
                and value.traceTypeDefinitionId
                == record.traceTypeDefinitionId
            )
        )
        recordIndex = next(
            index
            for index, (operation, value) in enumerate(destination.operations)
            if operation == "record" and value.eventId == record.eventId
        )
        assert definitionIndex < recordIndex

        if record.spanId is None:
            assert record.origin is not None
            continue

        start = startsBySpanId[record.spanId]
        assert record.spanStartEventId == start.eventId

        if record.kind == "spanStart" and record.parentSpanId is not None:
            assert record.parentSpanId in startsBySpanId
