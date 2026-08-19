# file: tests/backend/tracing/test_recordFactory.py ; version: 2
from __future__ import annotations

import pytest

from backend.tracing import (
    TRACE_EVENT,
    TRACE_SPAN,
    TraceCorrelationContext,
    TraceEventId,
    TraceInvariantError,
    TraceRecord,
    TraceSpanContext,
)
from backend.tracing.recordFactory import TraceRecordFactory


def _createParentlessEvent(
    factory: TraceRecordFactory,
    *,
    origin: str | None = "actant.test",
) -> TraceRecord:
    definition = TRACE_EVENT.getDefinition()
    generated = definition.event
    if generated is None:
        pytest.fail("TRACE_EVENT definition must contain event metadata.")

    return factory.createEvent(
        domain=definition.domain,
        recordType=generated.label,
        traceTypeDefinitionId=definition.traceTypeDefinitionId,
        level=generated.level,
        message="",
        attributes=None,
        exceptionSnapshot=None,
        spanContext=None,
        origin=origin,
        causedBy=(),
        correlations=TraceCorrelationContext(),
    )


def _createRootSpanStart(
    factory: TraceRecordFactory,
    *,
    origin: str | None = "actant.test",
) -> TraceRecord:
    definition = TRACE_SPAN.getDefinition()
    generated = definition.started
    if generated is None:
        pytest.fail("TRACE_SPAN definition must contain start metadata.")

    return factory.createSpanStart(
        spanId=factory.newSpanId(),
        spanFamily=definition.name,
        parentContext=None,
        domain=definition.domain,
        recordType=f"{definition.name}.{generated.label}",
        traceTypeDefinitionId=definition.traceTypeDefinitionId,
        level=generated.level,
        message="",
        attributes=None,
        exceptionSnapshot=None,
        origin=origin,
        causedBy=(),
        correlations=TraceCorrelationContext(),
    )


def testRejectedEventRecordDoesNotConsumeProducerSequence() -> None:
    factory = TraceRecordFactory()

    with pytest.raises(ValueError):  # noqa: PT011
        _createParentlessEvent(
            factory,
            origin=None,
        )

    record = _createParentlessEvent(factory)

    assert record.sequence == 1


def testRejectedSpanStartRecordDoesNotConsumeProducerSequence() -> None:
    factory = TraceRecordFactory()

    with pytest.raises(ValueError):  # noqa: PT011
        _createRootSpanStart(
            factory,
            origin=None,
        )

    record = _createRootSpanStart(factory)

    assert record.sequence == 1


def testRejectedAttributesDoNotConsumeProducerSequence() -> None:
    factory = TraceRecordFactory()
    definition = TRACE_EVENT.getDefinition()
    generated = definition.event
    if generated is None:
        pytest.fail("TRACE_EVENT definition must contain event metadata.")

    with pytest.raises(TypeError):
        factory.createEvent(
            domain=definition.domain,
            recordType=generated.label,
            traceTypeDefinitionId=definition.traceTypeDefinitionId,
            level=generated.level,
            message="",
            attributes={"unsupported": object()},
            exceptionSnapshot=None,
            spanContext=None,
            origin="actant.test",
            causedBy=(),
            correlations=TraceCorrelationContext(),
        )

    record = _createParentlessEvent(factory)

    assert record.sequence == 1


def testBackwardSpanEndMonotonicTimeIsInvariantFailure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = TraceRecordFactory()
    definition = TRACE_SPAN.getDefinition()
    completed = TRACE_SPAN.completed

    spanContext = TraceSpanContext(
        traceProducerId=factory.getTraceProducerId(),
        spanId=factory.newSpanId(),
        spanStartEventId=TraceEventId.new(),
        spanFamily=definition.name,
        correlations=TraceCorrelationContext(),
    )

    monkeypatch.setattr(
        "backend.tracing.recordFactory.time.monotonic_ns",
        lambda: 99,
    )

    with pytest.raises(
        TraceInvariantError,
        match="precedes its start timestamp",
    ):
        factory.createSpanEnd(
            spanContext=spanContext,
            domain=definition.domain,
            recordType=f"{definition.name}.{completed.label}",
            traceTypeDefinitionId=definition.traceTypeDefinitionId,
            level=completed.level,
            message="",
            attributes=None,
            exceptionSnapshot=None,
            outcome="completed",
            startedMonotonicNs=100,
            causedBy=(),
            correlations=TraceCorrelationContext(),
        )


def testBackwardSpanEndFailureDoesNotConsumeProducerSequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = TraceRecordFactory()
    definition = TRACE_SPAN.getDefinition()
    completed = TRACE_SPAN.completed

    spanContext = TraceSpanContext(
        traceProducerId=factory.getTraceProducerId(),
        spanId=factory.newSpanId(),
        spanStartEventId=TraceEventId.new(),
        spanFamily=definition.name,
        correlations=TraceCorrelationContext(),
    )

    monotonicValues = iter((99, 101))

    monkeypatch.setattr(
        "backend.tracing.recordFactory.time.monotonic_ns",
        lambda: next(monotonicValues),
    )

    with pytest.raises(TraceInvariantError):
        factory.createSpanEnd(
            spanContext=spanContext,
            domain=definition.domain,
            recordType=f"{definition.name}.{completed.label}",
            traceTypeDefinitionId=definition.traceTypeDefinitionId,
            level=completed.level,
            message="",
            attributes=None,
            exceptionSnapshot=None,
            outcome="completed",
            startedMonotonicNs=100,
            causedBy=(),
            correlations=TraceCorrelationContext(),
        )

    record = _createParentlessEvent(factory)

    assert record.sequence == 1
    assert record.timestampMonotonicNs == 101  # noqa: PLR2004
