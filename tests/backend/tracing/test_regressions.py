# file: tests/backend/tracing/test_regressions.py ; version: 1
from __future__ import annotations

import threading
from dataclasses import dataclass
from io import StringIO

from backend.tracing import (
    TraceEmergencyReporter,
    TraceEventType,
    Tracer,
    TraceRecord,
    TraceRecursivePublicationError,
    TraceTypeDefinition,
)
from tests.backend.tracing.helpers import CollectingDestination


@dataclass(slots=True)
class RecursiveDestination:
    tracer: Tracer
    enabled: bool = False
    error: Exception | None = None

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        return

    def write(self, record: TraceRecord) -> None:
        if not self.enabled:
            return

        try:
            self.tracer.event(
                TraceEventType(name="destination.recursive"),
            ).emit()
        except Exception as err:  # noqa: BLE001
            self.error = err


def _ordinaryRecords(destination: CollectingDestination) -> list[TraceRecord]:
    return [
        record
        for record in destination.records
        if not record.type.startswith("trace.producer-")
        and not record.type.startswith("trace.destination-")
    ]


def testNestedSpanRelationshipsRemainStructurallyCorrect() -> None:
    collector = CollectingDestination()
    tracer = Tracer(destinations=(collector,), origin="actant.test")

    parent = tracer.span().start()
    tracer.event().message("inside parent").emit()
    child = tracer.span().start()
    child.complete()
    parent.complete()

    records = _ordinaryRecords(collector)
    parentStart, event, childStart, childEnd, parentEnd = records

    assert event.spanId == parentStart.spanId
    assert childStart.parentSpanId == parentStart.spanId
    assert childEnd.spanId == childStart.spanId
    assert parentEnd.spanId == parentStart.spanId
    tracer.close()


def testRecursiveDestinationStillCannotTraceThroughOwningTracer() -> None:
    emergencyStream = StringIO()
    collector = CollectingDestination()
    tracer = Tracer(
        destinations=(collector,),
        origin="actant.test",
        emergencyReporter=TraceEmergencyReporter(stream=emergencyStream),
    )
    recursive = RecursiveDestination(tracer)
    tracer.addDestination(recursive)

    recursive.enabled = True
    tracer.emitEvent(message="ordinary")

    assert isinstance(recursive.error, TraceRecursivePublicationError)
    assert all(
        definition.name != "destination.recursive"
        for definition in tracer.getTraceTypeDefinitions().values()
    )
    assert "TRACE RECURSION" in emergencyStream.getvalue()

    tracer.removeDestination(recursive)
    tracer.close()


def testConcurrentEmissionKeepsWholeProducerSequenceContiguous() -> None:
    collector = CollectingDestination()
    tracer = Tracer(destinations=(collector,), origin="actant.test")
    threadCount = 4
    recordsPerThread = 25
    barrier = threading.Barrier(threadCount)
    errors: list[BaseException] = []
    errorsLock = threading.Lock()

    def emitRecords() -> None:
        try:
            barrier.wait()
            for _ in range(recordsPerThread):
                tracer.emitEvent()
        except BaseException as err:  # noqa: BLE001
            with errorsLock:
                errors.append(err)

    threads = [threading.Thread(target=emitRecords) for _ in range(threadCount)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    sequences = [record.sequence for record in collector.records]
    assert sequences == list(range(1, len(sequences) + 1))
    assert len(_ordinaryRecords(collector)) == threadCount * recordsPerThread
    tracer.close()


def testEveryPublishedRecordStillHasDefinitionDeliveredFirst() -> None:
    collector = CollectingDestination()
    tracer = Tracer(destinations=(collector,), origin="actant.test")
    customType = TraceEventType(name="pipeline.decision", domain="pipeline")

    tracer.emitEvent(traceType=customType, message="yes")

    for record in collector.records:
        definitionIndex = next(
            index
            for index, (operation, value) in enumerate(collector.operations)
            if (
                operation == "definition"
                and isinstance(value, TraceTypeDefinition)
                and value.traceTypeDefinitionId == record.traceTypeDefinitionId
            )
        )
        recordIndex = next(
            index
            for index, (operation, value) in enumerate(collector.operations)
            if (
                operation == "record"
                and isinstance(value, TraceRecord)
                and value.eventId == record.eventId
            )
        )
        assert definitionIndex < recordIndex

    tracer.close()
