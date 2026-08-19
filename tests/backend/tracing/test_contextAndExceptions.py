# file: tests/backend/tracing/test_contextAndExceptions.py ; version: 3
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from backend.core.ids import Uuid7Id
from backend.tracing import (
    Tracer,
    TraceSpanOwnershipError,
    captureExceptionSnapshot,
)
from tests.backend.tracing.helpers import CollectingDestination, createSinkTracer


@dataclass(frozen=True, slots=True)
class ActantRunId(Uuid7Id): # TODO: replace when real class exists
    pass


@dataclass(frozen=True, slots=True)
class ApplicationId(Uuid7Id): # TODO: replace when real class exists
    pass


def testCorrelationContextIsAppliedAndTransferredWithSpan() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    actantRunId = ActantRunId.new()
    applicationId = ApplicationId.new()

    with tracer.correlations(actantRunId=actantRunId):
        span = tracer.span().start()
        context = span.getContext()
        span.complete()

    with tracer.correlations(applicationId=applicationId):
        tracer.event().span(context).emit()

    lateRecord = collector.records[-1]
    assert lateRecord.actantRunId == actantRunId
    assert lateRecord.applicationId == applicationId


def testAnotherTaskCannotEndOwnedSpan() -> None:
    tracer = createSinkTracer()

    async def run() -> None:
        span = tracer.span().start()

        async def endFromAnotherTask() -> None:
            with pytest.raises(TraceSpanOwnershipError):
                span.complete()

        await asyncio.create_task(endFromAnotherTask())
        span.complete()

    asyncio.run(run())


def testExceptionSnapshotCapturesExceptionAndCatcherInformation() -> None:
    error = RuntimeError("broken")
    error.code = "E_BROKEN"  # ty: ignore[unresolved-attribute]
    error.add_note("observed at adapter boundary")

    snapshot = captureExceptionSnapshot(
        error,
        catcherAttributes={"adapter": "llama-cpp", "attempt": 2},
        includeStack=False,
    )

    assert snapshot.typeName == "RuntimeError"
    assert snapshot.message == "broken"
    assert snapshot.exceptionAttributes["code"] == "E_BROKEN"
    assert snapshot.catcherAttributes["attempt"] == 2
    assert snapshot.notes == ("observed at adapter boundary",)


def testManagedSpanCompletesOnNormalExit() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))

    with tracer.span().start():
        tracer.event().emit()

    assert collector.records[-1].kind == "spanEnd"
    assert collector.records[-1].outcome == "completed"


def testManagedSpanErrorsAndPropagatesEscapingException() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))

    with pytest.raises(RuntimeError, match="broken"), tracer.span().start():
        raise RuntimeError("broken")

    endRecord = collector.records[-1]
    assert endRecord.kind == "spanEnd"
    assert endRecord.outcome == "errored"
    assert endRecord.exceptionSnapshot is not None
    assert endRecord.exceptionSnapshot.message == "broken"


def testManagedSpanCancelsAndPropagatesCancellation() -> None:
    async def run() -> None:
        collector = CollectingDestination()
        tracer = Tracer(origin="actant.test", destinations=(collector,))

        with pytest.raises(asyncio.CancelledError):
            async with tracer.span().start():
                raise asyncio.CancelledError

        endRecord = collector.records[-1]
        assert endRecord.kind == "spanEnd"
        assert endRecord.outcome == "cancelled"
        assert endRecord.exceptionSnapshot is not None

    asyncio.run(run())


def testTransferredSpanPreservesExistingCorrelationAgainstAmbientConflict(
) -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    originalRunId = ActantRunId.new()
    conflictingRunId = ActantRunId.new()

    with tracer.correlations(actantRunId=originalRunId):
        span = tracer.span().start()
        context = span.getContext()
        span.complete()

    with tracer.correlations(actantRunId=conflictingRunId):
        tracer.event().span(context).emit()

    assert collector.records[-1].actantRunId == originalRunId
