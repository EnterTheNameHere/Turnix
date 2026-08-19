# file: tests/backend/tracing/test_destinationLifecycle.py ; version: 1
from __future__ import annotations

from dataclasses import dataclass, field
from io import StringIO
from typing import TYPE_CHECKING

from backend.tracing import TraceEmergencyReporter, Tracer
from tests.backend.tracing.helpers import CollectingDestination

if TYPE_CHECKING:
    from backend.tracing import TraceRecord, TraceTypeDefinition


@dataclass(slots=True)
class ToggleDestination:
    failDefinitions: bool = False
    failRecords: bool = False
    definitions: list[TraceTypeDefinition] = field(default_factory=list)
    records: list[TraceRecord] = field(default_factory=list)

    def writeTraceTypeDefinition(self, definition: TraceTypeDefinition) -> None:
        if self.failDefinitions:
            raise RuntimeError("definition unavailable")
        self.definitions.append(definition)

    def write(self, record: TraceRecord) -> None:
        if self.failRecords:
            raise RuntimeError("record unavailable")
        self.records.append(record)


def _recordsOfType(
    destination: CollectingDestination,
    recordType: str,
) -> list[TraceRecord]:
    return [record for record in destination.records if record.type == recordType]


def testDefinitionFailureIsReportedOnceUntilRegistrationRecovers() -> None:
    collector = CollectingDestination()
    emergencyStream = StringIO()
    tracer = Tracer(
        destinations=(collector,),
        origin="actant.test",
        emergencyReporter=TraceEmergencyReporter(stream=emergencyStream),
    )
    failing = ToggleDestination(failDefinitions=True)

    tracer.addDestination(failing)
    tracer.emitEvent()
    tracer.emitEvent()

    assert len(_recordsOfType(collector, "trace.destination-failed")) == 1
    assert emergencyStream.getvalue().count(
        "[ACTANT TRACE DESTINATION FAILURE]",
    ) == 1

    failing.failDefinitions = False
    tracer.emitEvent()
    tracer.emitEvent()

    assert len(_recordsOfType(collector, "trace.destination-recovered")) == 1
    assert emergencyStream.getvalue().count(
        "[ACTANT TRACE DESTINATION RECOVERED]",
    ) == 1
    assert not any(
        record.type == "trace.destination-failed"
        for record in failing.records
    )
    recovered = [
        record
        for record in failing.records
        if record.type == "trace.destination-recovered"
    ]
    assert len(recovered) == 1
    assert recovered[0].attributes["failedOperation"] == "writeTraceTypeDefinition"

    tracer.close()


def testRecordFailureUsesSameEdgeTriggeredHealthEpisode() -> None:
    collector = CollectingDestination()
    tracer = Tracer(destinations=(collector,), origin="actant.test")
    failing = ToggleDestination(failRecords=True)

    tracer.addDestination(failing)
    tracer.emitEvent()
    tracer.emitEvent()

    assert len(_recordsOfType(collector, "trace.destination-failed")) == 1

    failing.failRecords = False
    tracer.emitEvent()

    assert len(_recordsOfType(collector, "trace.destination-recovered")) == 1
    tracer.close()


def testSameDestinationGetsIndependentRegistrationPerTracer() -> None:
    shared = CollectingDestination()
    first = Tracer(destinations=(shared,), origin="actant.first")
    second = Tracer(destinations=(shared,), origin="actant.second")

    added = _recordsOfType(shared, "trace.destination-added")
    assert len(added) == 2  # noqa: PLR2004
    assert added[0].attributes["registrationId"] != added[1].attributes[
        "registrationId"
    ]
    assert added[0].traceProducerId != added[1].traceProducerId

    first.close()
    second.close()


def testRemoveAndReaddCreatesNewRegistrationIdentity() -> None:
    collector = CollectingDestination()
    target = ToggleDestination()
    tracer = Tracer(destinations=(collector,), origin="actant.test")

    tracer.addDestination(target)
    firstAdded = _recordsOfType(collector, "trace.destination-added")[-1]
    firstRegistrationId = firstAdded.attributes["registrationId"]

    assert tracer.removeDestination(target)
    removed = _recordsOfType(collector, "trace.destination-removed")[-1]
    assert removed.attributes["registrationId"] == firstRegistrationId

    tracer.addDestination(target)
    secondAdded = _recordsOfType(collector, "trace.destination-added")[-1]
    assert secondAdded.attributes["registrationId"] != firstRegistrationId

    tracer.close()


def testRemovingFailedDestinationCarriesFailureEpisodeSummary() -> None:
    collector = CollectingDestination()
    target = ToggleDestination(failRecords=True)
    tracer = Tracer(destinations=(collector,), origin="actant.test")

    tracer.addDestination(target)
    assert tracer.removeDestination(target)

    removed = _recordsOfType(collector, "trace.destination-removed")[-1]
    assert removed.attributes["wasFailed"] is True
    assert removed.attributes["failedOperation"] == "write"
    assert removed.attributes["failureErrorMessage"] == "record unavailable"

    failureErrorType = removed.attributes["failureErrorType"]
    assert isinstance(failureErrorType, str)
    assert failureErrorType.endswith("RuntimeError")

    tracer.close()
