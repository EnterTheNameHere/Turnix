# file: tests/backend/tracing/test_publisher.py ; version: 3
from __future__ import annotations

from io import StringIO

import pytest

from backend.tracing import (
    TraceEmergencyReporter,
    TraceEventId,
    TraceEventType,
    TraceInvariantError,
    TraceProducerId,
    Tracer,
    TraceRecord,
    TraceRecursivePublicationError,
    TraceTypeDefinition,
)
from backend.tracing.publisher import TracePublisher
from tests.backend.tracing.helpers import CollectingDestination, createSinkTracer


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
        self.enabled = False
        self.records = 0

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        return

    def write(self, record: TraceRecord) -> None:
        if not self.enabled:
            return

        self.records += 1
        self.tracer.event().message("recursive").emit()


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


class ToggleTargetDefinitionDestination:
    def __init__(self, targetName: str) -> None:
        self._targetName = targetName
        self.failTargetDefinition = True
        self.definitionAttempts = 0
        self.successfulDefinitions: list[TraceTypeDefinition] = []
        self.records: list[TraceRecord] = []

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        if definition.name == self._targetName:
            self.definitionAttempts += 1

            if self.failTargetDefinition:
                raise RuntimeError("intentional definition failure")

        self.successfulDefinitions.append(definition)

    def write(self, record: TraceRecord) -> None:
        self.records.append(record)


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


class ClosingDestination:
    def __init__(self, tracer: Tracer) -> None:
        self.tracer = tracer
        self.enabled = False

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        return

    def write(self, record: TraceRecord) -> None:
        if self.enabled:
            self.tracer.close()


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

    tracer.event().message("ordinary-event").emit()

    ordinaryRecords = [
        record
        for record in collector.records
        if record.message == "ordinary-event"
    ]

    assert len(ordinaryRecords) == 1
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

    recursive.enabled = True
    tracer.event().message("outer").emit()

    assert recursive.records == 1
    assert any(
        record.message == "outer"
        for record in collector.records
    )
    assert not any(
        record.message == "recursive"
        for record in collector.records
    )
    assert "TRACE RECURSION" in emergencyStream.getvalue()


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

    orderedRecords = sorted(
        collector.records,
        key=lambda record: record.sequence,
    )
    assert [record.sequence for record in orderedRecords] == list(
        range(1, len(orderedRecords) + 1),
    )


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

    closing.enabled = True
    tracer.event().message("before-remove").emit()

    tracer.removeDestination(closing)
    tracer.event().message("after-remove").emit()

    ordinaryMessages = [
        record.message
        for record in collector.records
        if record.message in ("before-remove", "after-remove")
    ]

    assert ordinaryMessages == [
        "before-remove",
        "after-remove",
    ]

    output = emergencyStream.getvalue()
    assert "TRACE RECURSION" in output
    assert "DESTINATION FAILURE" in output


def testPublisherDoesNotRedeliverSameDefinitionToActiveDestination() -> None:
    eventType = TraceEventType(name="publisher.definition")
    definition = eventType.getDefinition()
    activeDefinitions = {
        definition.traceTypeDefinitionId: definition,
    }
    collector = CollectingDestination()
    emergencyStream = StringIO()

    publisher = TracePublisher(
        getTraceTypeDefinitions=lambda: activeDefinitions,
        destinations=(collector,),
        emergencyReporter=TraceEmergencyReporter(stream=emergencyStream),
    )
    publisher.publishTraceTypeDefinition(definition)
    publisher.publishTraceTypeDefinition(definition)

    assert collector.definitions == [definition]


def testRemovedAndReaddedDestinationReceivesCurrentDefinitionsAgain() -> None:
    collector = CollectingDestination()
    keeper = CollectingDestination()
    tracer = Tracer(
        origin="actant.test",
        destinations=(collector, keeper),
    )
    initialCount = len(collector.definitions)

    assert tracer.removeDestination(collector)
    tracer.addDestination(collector)

    assert len(collector.definitions) == initialCount * 2


def testFailedDefinitionDeliveryIsRetriedBeforeLaterRecord() -> None:
    emergencyStream = StringIO()
    eventType = TraceEventType(name="publisher.retry-definition")
    destination = ToggleTargetDefinitionDestination(eventType.name)
    tracer = Tracer(
        origin="actant.test",
        destinations=(destination,),
        emergencyReporter=TraceEmergencyReporter(stream=emergencyStream),
    )

    tracer.event(eventType).message("first").emit()

    firstAttemptCount = destination.definitionAttempts
    assert firstAttemptCount > 0
    assert not any(
        record.message == "first"
        for record in destination.records
    )

    destination.failTargetDefinition = False
    tracer.event(eventType).message("second").emit()

    assert destination.definitionAttempts > firstAttemptCount

    secondRecords = [
        record
        for record in destination.records
        if record.message == "second"
    ]
    assert len(secondRecords) == 1

    secondRecord = secondRecords[0]
    assert any(
        definition.traceTypeDefinitionId
        == secondRecord.traceTypeDefinitionId
        for definition in destination.successfulDefinitions
    )
    assert "DESTINATION FAILURE" in emergencyStream.getvalue()


def testPublisherRejectsRecordWithUnregisteredDefinition() -> None:
    eventType = TraceEventType(name="publisher.unregistered")
    definition = eventType.getDefinition()
    generated = definition.event

    if generated is None:
        pytest.fail("Event definition must contain event metadata.")

    collector = CollectingDestination()
    emergencyStream = StringIO()

    publisher = TracePublisher(
        getTraceTypeDefinitions=lambda: {},  # noqa: PIE807
        destinations=(collector,),
        emergencyReporter=TraceEmergencyReporter(stream=emergencyStream),
    )

    record = TraceRecord(
        eventId=TraceEventId.new(),
        traceProducerId=TraceProducerId.new(),
        sequence=1,
        timestampUnixNs=1,
        timestampMonotonicNs=1,
        kind="event",
        domain=definition.domain,
        type=generated.label,
        traceTypeDefinitionId=definition.traceTypeDefinitionId,
        level=generated.level,
        origin="actant.test",
    )

    with pytest.raises(TraceInvariantError):
        publisher.publish(record)

    assert collector.records == []


def testDefinitionDeliveryProcessControlBaseExceptionPropagates() -> None:
    destination = InterruptingDefinitionDestination()

    with pytest.raises(KeyboardInterrupt):
        Tracer(
            origin="actant.test",
            destinations=(destination,),
        )


def testDestinationCannotRecursivelyTraceDuringDefinitionDelivery() -> None:
    tracer = createSinkTracer()
    recursive = RecursiveDefinitionDestination(tracer)

    tracer.addDestination(recursive)

    assert isinstance(
        recursive.error,
        TraceRecursivePublicationError,
    )
