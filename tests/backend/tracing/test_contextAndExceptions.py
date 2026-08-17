# file: tests/backend/tracing/test_contextAndExceptions.py ; version: 2
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
from tests.backend.tracing.helpers import CollectingDestination


@dataclass(frozen=True, slots=True)
class ActantRunId(Uuid7Id): # TODO: replace when real class exists
    pass


@dataclass(frozen=True, slots=True)
class ApplicationId(Uuid7Id): # TODO: replace when real class exists
    pass


def testCorrelationContextIsAppliedAndTransferredWithSpan() -> None:
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))
    actantRunId = ActantRunId.new()
    applicationId = ApplicationId.new()

    with tracer.correlations(actantRunId=actantRunId):
        span = tracer.span().start()
        context = span.getContext()
        span.complete()

    with tracer.correlations(applicationId=applicationId):
        tracer.event().span(context).emit()

    lateRecord = destination.records[-1]
    assert lateRecord.actantRunId == actantRunId
    assert lateRecord.applicationId == applicationId


def testAnotherTaskCannotEndOwnedSpan() -> None:
    tracer = Tracer(origin="actant.test")

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
    error.code = "E_BROKEN"
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
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))

    with tracer.span().start():
        tracer.event().emit()

    assert destination.records[-1].kind == "spanEnd"
    assert destination.records[-1].outcome == "completed"


def testManagedSpanErrorsAndPropagatesEscapingException() -> None:
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))

    with pytest.raises(RuntimeError, match="broken"), tracer.span().start():
        raise RuntimeError("broken")

    endRecord = destination.records[-1]
    assert endRecord.kind == "spanEnd"
    assert endRecord.outcome == "errored"
    assert endRecord.exceptionSnapshot is not None
    assert endRecord.exceptionSnapshot.message == "broken"


def testManagedSpanCancelsAndPropagatesCancellation() -> None:
    async def run() -> None:
        destination = CollectingDestination()
        tracer = Tracer(origin="actant.test", destinations=(destination,))

        with pytest.raises(asyncio.CancelledError):
            async with tracer.span().start():
                raise asyncio.CancelledError

        endRecord = destination.records[-1]
        assert endRecord.kind == "spanEnd"
        assert endRecord.outcome == "cancelled"
        assert endRecord.exceptionSnapshot is not None

    asyncio.run(run())


def testTransferredSpanPreservesExistingCorrelationAgainstAmbientConflict(
) -> None:
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))
    originalRunId = ActantRunId.new()
    conflictingRunId = ActantRunId.new()

    with tracer.correlations(actantRunId=originalRunId):
        span = tracer.span().start()
        context = span.getContext()
        span.complete()

    with tracer.correlations(actantRunId=conflictingRunId):
        tracer.event().span(context).emit()

    assert destination.records[-1].actantRunId == originalRunId
