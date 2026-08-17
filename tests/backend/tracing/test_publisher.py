# file: tests/backend/tracing/test_publisher.py ; version: 2
from __future__ import annotations

from io import StringIO

import pytest

from backend.tracing import (
    TraceEmergencyReporter,
    TraceEventId,
    TraceEventType,
    TraceInvariantError,
    TraceProducerId,
    TracePublisher,
    Tracer,
    TraceRecord,
    TraceRecursivePublicationError,
    TraceTypeDefinition,
)
from tests.backend.tracing.helpers import CollectingDestination


class FailingDestination:
    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        raise RuntimeError("definition failure")

    def write(self, record: TraceRecord) -> None:
        raise RuntimeError("record failure")


class RecursiveDestination:
    def __init__(self, tracer: Tracer) -> None:
        self.tracer = tracer
        self.records = 0

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        return

    def write(self, record: TraceRecord) -> None:
        self.records += 1
        self.tracer.event().message("recursive").emit()


class FailTargetDefinitionTwiceDestination:
    def __init__(self, targetName: str) -> None:
        self._targetName = targetName
        self._remainingFailures = 2
        self.definitionAttempts = 0
        self.successfulDefinitions: list[TraceTypeDefinition] = []
        self.records: list[TraceRecord] = []

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        if definition.name == self._targetName:
            self.definitionAttempts += 1

            if self._remainingFailures > 0:
                self._remainingFailures -= 1
                raise RuntimeError("intentional definition failure")

        self.successfulDefinitions.append(definition)

    def write(self, record: TraceRecord) -> None:
        self.records.append(record)


class InterruptingDefinitionDestination:
    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        raise KeyboardInterrupt

    def write(self, record: TraceRecord) -> None:
        return


class RecursiveDefinitionDestination:
    def __init__(self, tracer: Tracer) -> None:
        self.tracer = tracer
        self.error: Exception | None = None

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        try:
            self.tracer.event().emit()
        except Exception as err:  # noqa: BLE001
            self.error = err

    def write(self, record: TraceRecord) -> None:
        return


def testNewDestinationReceivesDefinitionsBeforeLaterRecords() -> None:
    first = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(first,))
    tracer.event(TraceEventType(name="pipeline.ready")).emit()

    second = CollectingDestination()
    tracer.addDestination(second)
    tracer.event(TraceEventType(name="pipeline.after-add")).emit()

    firstRecordIndex = next(
        index
        for index, (operation, _) in enumerate(second.operations)
        if operation == "record"
    )
    assert all(
        operation == "definition"
        for operation, _ in second.operations[:firstRecordIndex]
    )
    referencedRecord = second.records[-1]
    assert any(
        definition.traceTypeDefinitionId
        == referencedRecord.traceTypeDefinitionId
        for definition in second.definitions
    )


def testDestinationFailuresStayLocalAndOtherDestinationsReceiveRecord(
) -> None:
    emergencyStream = StringIO()
    collector = CollectingDestination()
    tracer = Tracer(
        origin="actant.test",
        destinations=(FailingDestination(), collector),
        emergencyReporter=TraceEmergencyReporter(stream=emergencyStream),
    )

    tracer.event().emit()

    assert len(collector.records) == 1
    assert "DESTINATION FAILURE" in emergencyStream.getvalue()


def testDestinationCannotRecursivelyPublishOrdinaryTracing() -> None:
    emergencyStream = StringIO()
    collector = CollectingDestination()
    tracer = Tracer(
        origin="actant.test",
        destinations=(collector,),
        emergencyReporter=TraceEmergencyReporter(stream=emergencyStream),
    )
    recursive = RecursiveDestination(tracer)
    tracer.addDestination(recursive)

    tracer.event().emit()

    assert recursive.records == 1
    assert len(collector.records) == 1
    assert "TRACE RECURSION" in emergencyStream.getvalue()


class RecursiveSpanEndDestination:
    def __init__(self) -> None:
        self.span = None
        self.error: Exception | None = None

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        return

    def write(self, record: TraceRecord) -> None:
        if self.span is None or record.kind != "event":
            return
        try:
            self.span.complete()
        except Exception as err:  # noqa: BLE001
            self.error = err


def testDestinationCannotEndActiveSpanDuringPublication() -> None:
    collector = CollectingDestination()
    recursive = RecursiveSpanEndDestination()
    tracer = Tracer(
        origin="actant.test",
        destinations=(collector, recursive),
    )
    span = tracer.span().start()
    recursive.span = span

    tracer.event().emit()

    assert isinstance(recursive.error, TraceRecursivePublicationError)
    assert tracer.getActiveSpanCount() == 1
    tracer.removeDestination(recursive)
    span.complete()
    assert [record.sequence for record in collector.records] == [1, 2, 3]


class ClosingDestination:
    def __init__(self, tracer: Tracer) -> None:
        self.tracer = tracer

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        return

    def write(self, record: TraceRecord) -> None:
        self.tracer.close()


def testDestinationCannotCloseTracerDuringPublication() -> None:
    emergencyStream = StringIO()
    collector = CollectingDestination()
    tracer = Tracer(
        origin="actant.test",
        destinations=(collector,),
        emergencyReporter=TraceEmergencyReporter(stream=emergencyStream),
    )
    closing = ClosingDestination(tracer)
    tracer.addDestination(closing)

    tracer.event().emit()
    tracer.removeDestination(closing)
    tracer.event().emit()

    assert [record.sequence for record in collector.records] == [1, 2]
    output = emergencyStream.getvalue()
    assert "TRACE RECURSION" in output
    assert "DESTINATION FAILURE" in output


def testPublisherDoesNotRedeliverSameDefinitionToActiveDestination() -> None:
    eventType = TraceEventType(name="publisher.definition")
    definition = eventType.getDefinition()
    activeDefinitions = {
        definition.traceTypeDefinitionId: definition,
    }
    destination = CollectingDestination()

    publisher = TracePublisher(
        getTraceTypeDefinitions=lambda: activeDefinitions,
        destinations=(destination,),
    )
    publisher.publishTraceTypeDefinition(definition)
    publisher.publishTraceTypeDefinition(definition)

    assert destination.definitions == [definition]


def testRemovedAndReaddedDestinationReceivesCurrentDefinitionsAgain() -> None:
    destination = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(destination,))
    initialCount = len(destination.definitions)

    assert tracer.removeDestination(destination)
    tracer.addDestination(destination)

    assert len(destination.definitions) == initialCount * 2


def testFailedDefinitionDeliveryIsRetriedBeforeLaterRecord() -> None:
    emergencyStream = StringIO()
    eventType = TraceEventType(name="publisher.retry-definition")
    destination = FailTargetDefinitionTwiceDestination(eventType.name)
    tracer = Tracer(
        origin="actant.test",
        destinations=(destination,),
        emergencyReporter=TraceEmergencyReporter(stream=emergencyStream),
    )

    tracer.event(eventType).message("first").emit()

    assert destination.definitionAttempts == 2  # noqa: PLR2004
    assert destination.records == []

    tracer.event(eventType).message("second").emit()

    assert destination.definitionAttempts == 3  # noqa: PLR2004
    assert len(destination.records) == 1
    assert destination.records[0].message == "second"
    assert any(
        definition.traceTypeDefinitionId
        == destination.records[0].traceTypeDefinitionId
        for definition in destination.successfulDefinitions
    )
    assert "DESTINATION FAILURE" in emergencyStream.getvalue()


def testPublisherRejectsRecordWithUnregisteredDefinition() -> None:
    eventType = TraceEventType(name="publisher.unregistered")
    definition = eventType.getDefinition()
    destination = CollectingDestination()

    publisher = TracePublisher(
        getTraceTypeDefinitions=lambda: {},
        destinations=(destination,),
    )

    record = TraceRecord(
        eventId=TraceEventId.new(),
        traceProducerId=TraceProducerId.new(),
        sequence=1,
        timestampUnixNs=1,
        timestampMonotonicNs=1,
        kind="event",
        domain=definition.domain,
        type=definition.event.label,  # ty: ignore[unresolved-attribute]
        traceTypeDefinitionId=definition.traceTypeDefinitionId,
        level=definition.event.level,  # ty: ignore[unresolved-attribute]
        origin="actant.test",
    )

    with pytest.raises(TraceInvariantError):
        publisher.publish(record)

    assert destination.records == []


def testDefinitionDeliveryProcessControlBaseExceptionPropagates() -> None:
    destination = InterruptingDefinitionDestination()

    with pytest.raises(KeyboardInterrupt):
        Tracer(
            origin="actant.test",
            destinations=(destination,),
        )


def testDestinationCannotRecursivelyTraceDuringDefinitionDelivery() -> None:
    tracer = Tracer(origin="actant.test")
    recursive = RecursiveDefinitionDestination(tracer)

    tracer.addDestination(recursive)

    assert isinstance(
        recursive.error,
        TraceRecursivePublicationError,
    )
