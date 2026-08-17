# file: tests/backend/tracing/test_traceRecords.py ; version: 1
from __future__ import annotations

from dataclasses import replace

import pytest

from backend.tracing import (
    TRACE_EVENT,
    TRACE_SPAN,
    TraceEventId,
    TraceProducerId,
    TraceRecord,
    TraceReference,
    TraceSpanId,
)


def _parentlessEvent() -> TraceRecord:
    definition = TRACE_EVENT.getDefinition()

    return TraceRecord(
        eventId=TraceEventId.new(),
        traceProducerId=TraceProducerId.new(),
        sequence=1,
        timestampUnixNs=100,
        timestampMonotonicNs=200,
        kind="event",
        domain="",
        type=TRACE_EVENT.event.label,  # ty: ignore[unresolved-attribute]
        traceTypeDefinitionId=definition.traceTypeDefinitionId,
        level=TRACE_EVENT.event.level,  # ty: ignore[unresolved-attribute]
        origin="test.runtime",
    )


def _rootSpanStart() -> TraceRecord:
    definition = TRACE_SPAN.getDefinition()
    eventId = TraceEventId.new()

    return TraceRecord(
        eventId=eventId,
        traceProducerId=TraceProducerId.new(),
        sequence=1,
        timestampUnixNs=100,
        timestampMonotonicNs=200,
        kind="spanStart",
        domain="",
        type=TRACE_SPAN.started.label,
        traceTypeDefinitionId=definition.traceTypeDefinitionId,
        level=TRACE_SPAN.started.level,
        spanId=TraceSpanId.new(),
        spanStartEventId=eventId,
        spanFamily=TRACE_SPAN.name,
        origin="test.runtime",
    )


def _spanEnd(startRecord: TraceRecord) -> TraceRecord:
    definition = TRACE_SPAN.getDefinition()

    return TraceRecord(
        eventId=TraceEventId.new(),
        traceProducerId=startRecord.traceProducerId,
        sequence=2,
        timestampUnixNs=150,
        timestampMonotonicNs=250,
        kind="spanEnd",
        domain="",
        type=TRACE_SPAN.completed.label,
        traceTypeDefinitionId=definition.traceTypeDefinitionId,
        level=TRACE_SPAN.completed.level,
        spanId=startRecord.spanId,
        spanStartEventId=startRecord.eventId,
        spanFamily=TRACE_SPAN.name,
        outcome="completed",
        durationNs=50,
    )


def testParentlessEventAcceptsOriginWithoutSpanLinkage() -> None:
    record = _parentlessEvent()

    assert record.kind == "event"
    assert record.origin == "test.runtime"
    assert record.spanId is None
    assert record.spanStartEventId is None


def testContainedEventAcceptsSpanLinkageWithoutOrigin() -> None:
    spanStart = _rootSpanStart()

    record = replace(
        _parentlessEvent(),
        spanId=spanStart.spanId,
        spanStartEventId=spanStart.eventId,
        origin=None,
    )

    assert record.spanId == spanStart.spanId
    assert record.spanStartEventId == spanStart.eventId
    assert record.origin is None


def testParentlessEventRequiresOrigin() -> None:
    with pytest.raises(
        ValueError,
        match="Parentless event must contain origin",
    ):
        replace(
            _parentlessEvent(),
            origin=None,
        )


def testContainedEventRequiresSpanStartEventId() -> None:
    with pytest.raises(
        ValueError,
        match="Contained event must contain spanStartEventId",
    ):
        replace(
            _parentlessEvent(),
            spanId=TraceSpanId.new(),
            origin=None,
        )


def testContainedEventRejectsOrigin() -> None:
    spanStart = _rootSpanStart()

    with pytest.raises(
        ValueError,
        match="Contained event must not contain origin",
    ):
        replace(
            _parentlessEvent(),
            spanId=spanStart.spanId,
            spanStartEventId=spanStart.eventId,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "outcome",
            "completed",
            "Event record must not contain outcome",
        ),
        (
            "durationNs",
            1,
            "Event record must not contain durationNs",
        ),
        (
            "spanFamily",
            "trace.span",
            "Event record must not contain spanFamily",
        ),
        (
            "parentSpanId",
            TraceSpanId.new(),
            "Event record must not contain parentSpanId",
        ),
    ],
)
def testEventRejectsNonEventStructuralFields(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(
            _parentlessEvent(),
            **{field: value},
        )


def testRootSpanStartAcceptsOriginAndSelfStartEventIdentity() -> None:
    record = _rootSpanStart()

    assert record.kind == "spanStart"
    assert record.spanStartEventId == record.eventId
    assert record.spanId is not None
    assert record.spanFamily == TRACE_SPAN.name
    assert record.parentSpanId is None
    assert record.origin == "test.runtime"


def testChildSpanStartAcceptsParentWithoutOrigin() -> None:
    parent = _rootSpanStart()

    childEventId = TraceEventId.new()
    child = replace(
        parent,
        eventId=childEventId,
        spanId=TraceSpanId.new(),
        spanStartEventId=childEventId,
        parentSpanId=parent.spanId,
        origin=None,
    )

    assert child.parentSpanId == parent.spanId
    assert child.origin is None


def testSpanStartRequiresSpanId() -> None:
    with pytest.raises(
        ValueError,
        match="Span-start record must contain spanId",
    ):
        replace(
            _rootSpanStart(),
            spanId=None,
        )


def testSpanStartEventIdMustEqualEventId() -> None:
    with pytest.raises(
        ValueError,
        match="Span-start spanStartEventId must equal eventId",
    ):
        replace(
            _rootSpanStart(),
            spanStartEventId=TraceEventId.new(),
        )


def testSpanStartRequiresSpanFamily() -> None:
    with pytest.raises(
        ValueError,
        match="Span-start record must contain spanFamily",
    ):
        replace(
            _rootSpanStart(),
            spanFamily=None,
        )


def testRootSpanStartRequiresOrigin() -> None:
    with pytest.raises(
        ValueError,
        match="Root span-start record must contain origin",
    ):
        replace(
            _rootSpanStart(),
            origin=None,
        )


def testSpanStartRejectsSelfParent() -> None:
    record = _rootSpanStart()

    with pytest.raises(
        ValueError,
        match="Span-start record must not identify itself as parent span",
    ):
        replace(
            record,
            parentSpanId=record.spanId,
            origin=None,
        )


def testChildSpanStartRejectsOrigin() -> None:
    record = _rootSpanStart()

    with pytest.raises(
        ValueError,
        match="Child span-start record must not contain origin",
    ):
        replace(
            record,
            parentSpanId=TraceSpanId.new(),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "outcome",
            "completed",
            "Span-start record must not contain outcome",
        ),
        (
            "durationNs",
            1,
            "Span-start record must not contain durationNs",
        ),
    ],
)
def testSpanStartRejectsTerminalFields(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(
            _rootSpanStart(),
            **{field: value},
        )


def testSpanEndAcceptsTerminalStructureAndDuration() -> None:
    startRecord = _rootSpanStart()
    record = _spanEnd(startRecord)

    assert record.kind == "spanEnd"
    assert record.spanId == startRecord.spanId
    assert record.spanStartEventId == startRecord.eventId
    assert record.spanFamily == TRACE_SPAN.name
    assert record.outcome == "completed"
    assert record.durationNs == 50  # noqa: PLR2004


def testSpanEndAllowsMissingDuration() -> None:
    record = replace(
        _spanEnd(_rootSpanStart()),
        durationNs=None,
    )

    assert record.durationNs is None


def testSpanEndRequiresSpanId() -> None:
    with pytest.raises(
        ValueError,
        match="Span-end record must contain spanId",
    ):
        replace(
            _spanEnd(_rootSpanStart()),
            spanId=None,
        )


def testSpanEndRequiresSpanStartEventId() -> None:
    with pytest.raises(
        ValueError,
        match="Span-end record must contain spanStartEventId",
    ):
        replace(
            _spanEnd(_rootSpanStart()),
            spanStartEventId=None,
        )


def testSpanEndMustNotIdentifyItselfAsSpanStart() -> None:
    record = _spanEnd(_rootSpanStart())

    with pytest.raises(
        ValueError,
        match="Span-end record must not identify itself as span start",
    ):
        replace(
            record,
            spanStartEventId=record.eventId,
        )


def testSpanEndRejectsParentSpanId() -> None:
    with pytest.raises(
        ValueError,
        match="Span-end record must not contain parentSpanId",
    ):
        replace(
            _spanEnd(_rootSpanStart()),
            parentSpanId=TraceSpanId.new(),
        )


def testSpanEndRequiresSpanFamily() -> None:
    with pytest.raises(
        ValueError,
        match="Span-end record must contain spanFamily",
    ):
        replace(
            _spanEnd(_rootSpanStart()),
            spanFamily=None,
        )


def testSpanEndRejectsOrigin() -> None:
    with pytest.raises(
        ValueError,
        match="Span-end record must not contain origin",
    ):
        replace(
            _spanEnd(_rootSpanStart()),
            origin="test.runtime",
        )


def testSpanEndRequiresOutcome() -> None:
    with pytest.raises(
        ValueError,
        match="Span-end record must contain outcome",
    ):
        replace(
            _spanEnd(_rootSpanStart()),
            outcome=None,
        )


def testSpanEndRejectsNegativeDuration() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        replace(
            _spanEnd(_rootSpanStart()),
            durationNs=-1,
        )


def testTraceRecordRequiresExactTupleForCausedBy() -> None:
    reference = TraceReference(
        kind="test.entity",
        id="entity-1",
    )

    with pytest.raises(
        TypeError,
        match="causedBy must be an exact built-in tuple",
    ):
        replace(
            _parentlessEvent(),
            causedBy=[reference],
        )


def testTraceRecordRejectsDuplicateCausalReferences() -> None:
    reference = TraceReference(
        kind="test.entity",
        id="entity-1",
    )

    with pytest.raises(
        ValueError,
        match="causedBy must not contain duplicates",
    ):
        replace(
            _parentlessEvent(),
            causedBy=(reference, reference),
        )
