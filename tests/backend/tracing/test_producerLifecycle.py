# file: tests/backend/tracing/test_producerLifecycle.py ; version: 1
from __future__ import annotations

from backend.tracing import TraceProducerStartContext, Tracer
from tests.backend.tracing.helpers import CollectingDestination


def _recordTypes(destination: CollectingDestination) -> list[str]:
    return [record.type for record in destination.records]


def testProducerInitializationPublishesDestinationThenReadyEvidence() -> None:
    collector = CollectingDestination()
    tracer = Tracer(destinations=(collector,), origin="actant.test")

    assert _recordTypes(collector)[:2] == [
        "trace.destination-added",
        "trace.producer-ready",
    ]
    assert [record.sequence for record in collector.records] == list(
        range(1, len(collector.records) + 1),
    )
    assert collector.records[-1].traceProducerId == tracer.getTraceProducerId()

    tracer.close()


def testProducerReadyCarriesOwnerSuppliedPredecessorContext() -> None:
    firstCollector = CollectingDestination()
    first = Tracer(destinations=(firstCollector,), origin="actant.test")
    predecessor = first.getTraceProducerId()
    first.close()

    secondCollector = CollectingDestination()
    second = Tracer(
        destinations=(secondCollector,),
        origin="actant.test",
        startContext=TraceProducerStartContext(
            predecessorProducerId=predecessor,
            reason="recovery",
        ),
    )

    ready = next(
        record
        for record in secondCollector.records
        if record.type == "trace.producer-ready"
    )
    assert ready.traceProducerId == second.getTraceProducerId()
    assert ready.traceProducerId != predecessor
    assert ready.attributes["startReason"] == "recovery"
    assert len(ready.causedBy) == 1
    assert ready.causedBy[0].kind == "trace.producer"
    assert ready.causedBy[0].id == str(predecessor)

    second.close()


def testClosePublishesStoppingAbandonmentAndStoppedInOrder() -> None:
    collector = CollectingDestination()
    tracer = Tracer(destinations=(collector,), origin="actant.test")
    tracer.span().start()

    tracer.close()

    types = _recordTypes(collector)
    stoppingIndex = types.index("trace.producer-stopping")
    abandonedIndex = types.index("trace.span-abandoned")
    stoppedIndex = types.index("trace.producer-stopped")

    assert stoppingIndex < abandonedIndex < stoppedIndex
    assert tracer.getActiveSpanCount() == 0


def testRepeatedCloseDoesNotDuplicateProducerTerminalEvidence() -> None:
    collector = CollectingDestination()
    tracer = Tracer(destinations=(collector,), origin="actant.test")

    tracer.close()
    tracer.close()

    types = _recordTypes(collector)
    assert types.count("trace.producer-stopping") == 1
    assert types.count("trace.producer-stopped") == 1
