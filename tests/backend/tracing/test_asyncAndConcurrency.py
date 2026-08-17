# file: tests/backend/tracing/test_asyncAndConcurrency.py ; version: 2
from __future__ import annotations

import asyncio
import threading
from io import StringIO

import pytest

from backend.tracing import (
    TraceEmergencyReporter,
    TraceEventType,
    Tracer,
    TraceRecord,
    TraceRecursivePublicationError,
    TraceRuntimeContext,
    TraceTypeDefinition,
)
from tests.backend.tracing.helpers import CollectingDestination


class OneShotDelayedDestination:
    def __init__(self, tracer: Tracer) -> None:
        self._tracer = tracer
        self.task: asyncio.Task[None] | None = None
        self._scheduled = False

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        return

    def write(self, record: TraceRecord) -> None:
        if self._scheduled:
            return
        self._scheduled = True
        self.task = asyncio.create_task(self._emitLater())

    async def _emitLater(self) -> None:
        await asyncio.sleep(0)
        self._tracer.event().message("delayed").emit()


class NewTypeRecursiveDestination:
    def __init__(self, tracer: Tracer) -> None:
        self._tracer = tracer
        self.error: Exception | None = None

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        return

    def write(self, record: TraceRecord) -> None:
        try:
            self._tracer.event(
                TraceEventType(name="destination.recursive"),
            ).emit()
        except Exception as err:  # noqa: BLE001
            self.error = err


class InterruptingDestination:
    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        return

    def write(self, record: TraceRecord) -> None:
        raise KeyboardInterrupt


def testCopiedAsyncContextDoesNotStayPermanentlyMarkedAsPublishing() -> None:
    async def run() -> None:
        collector = CollectingDestination()
        tracer = Tracer(origin="actant.test", destinations=(collector,))
        delayed = OneShotDelayedDestination(tracer)
        tracer.addDestination(delayed)

        tracer.event().message("initial").emit()
        assert delayed.task is not None
        await delayed.task

        assert [record.message for record in collector.records] == [
            "initial",
            "delayed",
        ]

    asyncio.run(run())


def testRecursiveDestinationCannotRegisterTypeOrConsumeSequence() -> None:
    emergencyStream = StringIO()
    collector = CollectingDestination()
    tracer = Tracer(
        origin="actant.test",
        destinations=(collector,),
        emergencyReporter=TraceEmergencyReporter(stream=emergencyStream),
    )
    recursive = NewTypeRecursiveDestination(tracer)
    tracer.addDestination(recursive)

    tracer.event().message("first").emit()
    tracer.removeDestination(recursive)
    tracer.event().message("second").emit()

    assert isinstance(recursive.error, TraceRecursivePublicationError)
    assert all(
        definition.name != "destination.recursive"
        for definition in tracer.getTraceTypeDefinitions().values()
    )
    assert [record.sequence for record in collector.records] == [1, 2]
    assert "TRACE RECURSION" in emergencyStream.getvalue()


def testInheritedSpanContextCanEmitAndCreateChildInAnotherTask() -> None:
    async def run() -> None:
        collector = CollectingDestination()
        tracer = Tracer(origin="actant.test", destinations=(collector,))
        parent = tracer.span().start()

        async def childTask() -> None:
            tracer.event().message("from child task").emit()
            child = tracer.span().start()
            child.complete()

        await asyncio.create_task(childTask())
        parent.complete()

        parentStart, event, childStart, childEnd, parentEnd = collector.records
        assert event.spanId == parentStart.spanId
        assert childStart.parentSpanId == parentStart.spanId
        assert childEnd.spanId == childStart.spanId
        assert parentEnd.spanId == parentStart.spanId

    asyncio.run(run())


def testCloseUnwindsNestedAmbientSpansInReverseOrder() -> None:
    collector = CollectingDestination()
    runtimeContext = TraceRuntimeContext()
    tracer = Tracer(
        origin="actant.test",
        destinations=(collector,),
        runtimeContext=runtimeContext,
    )
    parent = tracer.span().start()
    child = tracer.span().start()

    tracer.close()

    assert runtimeContext.getCurrentSpan() is None
    assert tracer.getActiveSpanCount() == 0
    assert [record.spanId for record in collector.records[-2:]] == [
        child.spanId,
        parent.spanId,
    ]
    assert all(
        record.type == "trace.span-abandoned"
        for record in collector.records[-2:]
    )


def testDestinationProcessControlBaseExceptionPropagates() -> None:
    tracer = Tracer(
        origin="actant.test",
        destinations=(InterruptingDestination(),),
    )

    with pytest.raises(KeyboardInterrupt):
        tracer.event().emit()


def testConcurrentEmissionHaveUniqueContiguousProducerSequence() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))

    threadCount = 8
    recordsPerThread = 50
    barrier = threading.Barrier(threadCount)
    errors: list[BaseException] = []
    errorsLock = threading.Lock()

    def emitRecords() -> None:
        try:
            barrier.wait()
            for _ in range(recordsPerThread):
                tracer.event().emit()
        except BaseException as err:  # noqa: BLE001
            with errorsLock:
                errors.append(err)

    threads = [
        threading.Thread(target=emitRecords)
        for _ in range(threadCount)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []

    orderedRecords = sorted(
        collector.records,
        key=lambda record: record.sequence,
    )
    sequences = [record.sequence for record in orderedRecords]
    monotonicTimes = [record.timestampMonotonicNs for record in orderedRecords]
    expectedCount = threadCount * recordsPerThread

    assert len(sequences) == expectedCount
    assert sequences == list(range(1, expectedCount + 1))
    assert monotonicTimes == sorted(monotonicTimes)
