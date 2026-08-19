# file: tests/backend/tracing/test_asyncAndConcurrency.py ; version: 3
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
from tests.backend.tracing.helpers import CollectingDestination, checkpointDestination, recordsAfter


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


def testCopiedAsyncContextDoesNotStayPermanentlyMarkedAsPublishing() -> None:
    async def run() -> None:
        collector = CollectingDestination()
        tracer = Tracer(origin="actant.test", destinations=(collector,))
        delayed = OneShotDelayedDestination(tracer)
        tracer.addDestination(delayed)

        tracer.event().message("initial").emit()
        assert delayed.task is not None
        await delayed.task

        ordinaryMessages = [
            record.message
            for record in collector.records
            if record.message in {"initial", "delayed"}
        ]

        assert ordinaryMessages == [
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

    orderedRecords = sorted(
        collector.records,
        key=lambda record: record.sequence,
    )
    assert [record.sequence for record in orderedRecords] == list(
        range(1, len(orderedRecords) + 1),
    )

    ordinaryMessages = [
        record.message
        for record in orderedRecords
        if record.message in {"first", "second"}
    ]
    assert ordinaryMessages == ["first", "second"]

    assert "TRACE RECURSION" in emergencyStream.getvalue()


def testInheritedSpanContextCanEmitAndCreateChildInAnotherTask() -> None:
    async def run() -> None:
        collector = CollectingDestination()
        tracer = Tracer(origin="actant.test", destinations=(collector,))
        checkpoint = checkpointDestination(collector)

        parent = tracer.span().start()

        async def childTask() -> None:
            tracer.event().message("from child task").emit()
            child = tracer.span().start()
            child.complete()

        await asyncio.create_task(childTask())
        parent.complete()

        parentStart, event, childStart, childEnd, parentEnd = recordsAfter(
            collector,
            checkpoint,
        )

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

    abandonmentRecords = [
        record
        for record in collector.records
        if record.type == "trace.span-abandoned"
    ]

    assert [record.spanId for record in abandonmentRecords] == [
        child.spanId,
        parent.spanId,
    ]


def testDestinationProcessControlBaseExceptionPropagates() -> None:
    class InterruptionDestination:
        def __init__(self) -> None:
            self.interrupt = False

        def writeTraceTypeDefinition(
            self,
            definition: TraceTypeDefinition,
        ) -> None:
            return

        def write(self, record: TraceRecord) -> None:
            if self.interrupt:
                raise KeyboardInterrupt

    destination = InterruptionDestination()
    tracer = Tracer(
        origin="actant.test",
        destinations=(destination,),
    )

    destination.interrupt = True

    with pytest.raises(KeyboardInterrupt):
        tracer.event().emit()


def testConcurrentEmissionsHaveUniqueContiguousProducerSequence() -> None:
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
                tracer.event().message("concurrent-test-event").emit()
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

    expectedEventCount = threadCount * recordsPerThread
    emittedRecords = [
        record
        for record in orderedRecords
        if record.message == "concurrent-test-event"
    ]

    assert len(emittedRecords) == expectedEventCount

    assert len(sequences) == len(set(sequences))
    assert sequences == list(range(1, len(sequences) + 1))
    assert monotonicTimes == sorted(monotonicTimes)
