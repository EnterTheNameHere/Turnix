# file: tests/backend/tracing/test_architecture.py ; version: 1
from __future__ import annotations

import inspect

import pytest

from backend import tracing
from backend.tracing import (
    TraceContextError,
    TraceDestinationStateError,
    Tracer,
)
from backend.tracing.context import TraceRuntimeContext
from backend.tracing.destinations import TraceSinkDestination


def testPackageSurfaceDoesNotExportOwnedMutableInternals() -> None:
    assert "TracePublisher" not in tracing.__all__
    assert "TraceRecordFactory" not in tracing.__all__
    assert "TraceTypeRegistry" not in tracing.__all__
    assert "TraceTypeRegistration" not in tracing.__all__


def testTracerRequiresExplicitDestination() -> None:
    with pytest.raises(ValueError, match="requires at least one destination"):
        Tracer(destinations=())


def testExplicitSinkMakesIntentionalDiscardValid() -> None:
    tracer = Tracer(destinations=(TraceSinkDestination(),))
    tracer.emitEvent(origin="actant.test")
    tracer.close()


def testTracerOwnsFreshProducerAndDoesNotExposePublisherOrFactoryInjection() -> None:
    first = Tracer(destinations=(TraceSinkDestination(),))
    second = Tracer(destinations=(TraceSinkDestination(),))

    assert first.getTraceProducerId() != second.getTraceProducerId()
    assert not hasattr(first, "getPublisher")
    assert "recordFactory" not in inspect.signature(Tracer).parameters

    first.close()
    second.close()


def testFinalDestinationCannotBeRemoved() -> None:
    destination = TraceSinkDestination()
    tracer = Tracer(destinations=(destination,))

    with pytest.raises(TraceDestinationStateError):
        tracer.removeDestination(destination)

    tracer.close()


def testSharedRuntimeContextDoesNotShareProducerStructure() -> None:
    runtimeContext = TraceRuntimeContext()
    first = Tracer(
        destinations=(TraceSinkDestination(),),
        origin="actant.first",
        runtimeContext=runtimeContext,
    )
    second = Tracer(
        destinations=(TraceSinkDestination(),),
        origin="actant.second",
        runtimeContext=runtimeContext,
    )

    span = first.span().start()
    context = span.getContext()

    with pytest.raises(TraceContextError):
        second.emitEvent(span=context)

    span.complete()
    first.close()
    second.close()
