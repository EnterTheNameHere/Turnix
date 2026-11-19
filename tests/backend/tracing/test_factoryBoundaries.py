# file: tests/backend/tracing/test_factoryBoundaries.py ; version: 1
from __future__ import annotations

import inspect

import pytest

from backend.tracing import TRACE_EVENT, TRACE_SPAN, TraceEventId
from backend.tracing.context import TraceCorrelationContext, TraceSpanContext
from backend.tracing.errors import TraceContextError
from backend.tracing.recordFactory import TraceRecordFactory


def _foreignSpanContext(
    owner: TraceRecordFactory,
) -> TraceSpanContext:
    return TraceSpanContext(
        traceProducerId=owner.getTraceProducerId(),
        spanId=owner.newSpanId(),
        spanStartEventId=TraceEventId.new(),
        spanFamily=TRACE_SPAN.name,
        correlations=TraceCorrelationContext(),
    )


def testFactoryAlwaysCreatesItsOwnProducerIdentity() -> None:
    assert "traceProducerId" not in inspect.signature(
        TraceRecordFactory,
    ).parameters
    assert (
        TraceRecordFactory().getTraceProducerId()
        != TraceRecordFactory().getTraceProducerId()
    )


def testFactoryRejectsForeignContextForEvent() -> None:
    factory = TraceRecordFactory()
    foreignFactory = TraceRecordFactory()
    definition = TRACE_EVENT.getDefinition()
    generated = definition.event

    if generated is None:
        pytest.fail("TRACE_EVENT definition must contain event metadata.")

    with pytest.raises(TraceContextError):
        factory.createEvent(
            domain=definition.domain,
            recordType=generated.label,
            traceTypeDefinitionId=definition.traceTypeDefinitionId,
            level=generated.level,
            message="",
            attributes=None,
            exceptionSnapshot=None,
            spanContext=_foreignSpanContext(foreignFactory),
            origin=None,
            causedBy=(),
            correlations=TraceCorrelationContext(),
        )


def testFactoryRejectsForeignContextForSpanStart() -> None:
    factory = TraceRecordFactory()
    foreignFactory = TraceRecordFactory()
    definition = TRACE_SPAN.getDefinition()

    with pytest.raises(TraceContextError):
        factory.createSpanStart(
            spanId=factory.newSpanId(),
            spanFamily=definition.name,
            parentContext=_foreignSpanContext(foreignFactory),
            domain=definition.domain,
            recordType=definition.getStartedRecordType(),
            traceTypeDefinitionId=definition.traceTypeDefinitionId,
            level=TRACE_SPAN.started.level,
            message="",
            attributes=None,
            exceptionSnapshot=None,
            origin=None,
            causedBy=(),
            correlations=TraceCorrelationContext(),
        )


def testFactoryRejectsForeignContextForSpanEnd() -> None:
    factory = TraceRecordFactory()
    foreignFactory = TraceRecordFactory()
    definition = TRACE_SPAN.getDefinition()
    completed = TRACE_SPAN.completed

    with pytest.raises(TraceContextError):
        factory.createSpanEnd(
            spanContext=_foreignSpanContext(foreignFactory),
            domain=definition.domain,
            recordType=definition.getOutcomeRecordType("completed"),
            traceTypeDefinitionId=definition.traceTypeDefinitionId,
            level=completed.level,
            message="",
            attributes=None,
            exceptionSnapshot=None,
            outcome="completed",
            startedMonotonicNs=0,
            causedBy=(),
            correlations=TraceCorrelationContext(),
        )
