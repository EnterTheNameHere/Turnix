# file: tests/backend/tracing/test_spanLifecycleRecovery.py ; version: 2
from __future__ import annotations

import threading
from io import StringIO

import pytest

from backend.tracing import (
    TraceClosedError,
    TraceContextError,
    TraceEmergencyReporter,
    Tracer,
    TraceRecord,
    TraceSpanStateError,
    TraceTypeDefinition,
)
from tests.backend.tracing.helpers import CollectingDestination, checkpointDestination, recordsAfter


class BlockingRecordDestination:
    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._blocked = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        return

    def write(self, record: TraceRecord) -> None:
        if self._blocked or record.kind != self._kind:
            return

        self._blocked = True
        self.entered.set()

        if not self.release.wait(timeout=5):
            raise RuntimeError(
                "Timed out waiting to release blocked trace publication.",
            )


def testManagedParentRecoveryAbandonsAmbientChildBeforeParent() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    checkpoint = checkpointDestination(collector)

    with tracer.span().start() as parent:
        child = tracer.span().start()

    records = recordsAfter(collector, checkpoint)

    assert tracer.getActiveSpanCount() == 0
    assert [record.type for record in records] == [
        "trace.span.started",
        "trace.span.started",
        "trace.span-abandoned",
        "trace.span-abandoned",
    ]
    assert [record.spanId for record in records[-2:]] == [
        child.spanId,
        parent.spanId,
    ]

    with pytest.raises(TraceSpanStateError):
        child.complete()

    with pytest.raises(TraceSpanStateError):
        parent.complete()


def testManagedRecoveryUnwindsDeepAmbientStackInLifoOrder() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))

    with tracer.span().start() as parent:
        child = tracer.span().start()
        grandchild = tracer.span().start()

    abandonmentRecords = [
        record
        for record in collector.records
        if record.type == "trace.span-abandoned"
    ]

    assert tracer.getActiveSpanCount() == 0
    assert [record.spanId for record in abandonmentRecords] == [
        grandchild.spanId,
        child.spanId,
        parent.spanId,
    ]


def testManagedRecoveryFollowsAmbientLeaseStackNotStructuralParent() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))

    structuralParent = tracer.span().start()
    structuralParentContext = structuralParent.getContext()
    structuralParent.complete()

    with tracer.span().start() as ambientParent:
        child = (
            tracer.span()
            .parent(structuralParentContext)
            .start()
        )

    childStart = next(
        record
        for record in collector.records
        if (
            record.kind == "spanStart"
            and record.spanId == child.spanId
        )
    )
    abandonmentRecords = [
        record
        for record in collector.records
        if record.type == "trace.span-abandoned"
    ]

    assert childStart.parentSpanId == structuralParent.spanId
    assert childStart.parentSpanId != ambientParent.spanId
    assert [record.spanId for record in abandonmentRecords] == [
        child.spanId,
        ambientParent.spanId,
    ]
    assert tracer.getActiveSpanCount() == 0


def testCloseWaitsForAdmittedSpanStartThenAbandonsStartedSpan() -> None:
    collector = CollectingDestination()
    blocked = BlockingRecordDestination("spanStart")
    tracer = Tracer(origin="actant.test", destinations=(collector, blocked))

    spanHolder = []
    startErrors: list[BaseException] = []
    closeErrors: list[BaseException] = []

    def startSpan() -> None:
        try:
            spanHolder.append(tracer.span().start())
        except BaseException as err:  # noqa: BLE001
            startErrors.append(err)

    def closeTracer() -> None:
        try:
            tracer.close()
        except BaseException as err:  # noqa: BLE001
            closeErrors.append(err)

    startThread = threading.Thread(target=startSpan)
    startThread.start()

    assert blocked.entered.wait(timeout=5)

    closeThread = threading.Thread(target=closeTracer)
    closeThread.start()

    blocked.release.set()

    startThread.join(timeout=5)
    closeThread.join(timeout=5)

    assert not startThread.is_alive()
    assert not closeThread.is_alive()
    assert startErrors == []
    assert closeErrors == []
    assert len(spanHolder) == 1
    assert tracer.getActiveSpanCount() == 0

    span = spanHolder[0]
    relatedRecords = [
        record
        for record in collector.records
        if record.spanId == span.spanId
    ]

    assert [record.kind for record in relatedRecords] == [
        "spanStart",
        "event",
    ]
    assert relatedRecords[-1].type == "trace.span-abandoned"


def testCloseWaitsForAdmittedSpanEndAndDoesNotAlsoAbandonSpan() -> None:
    collector = CollectingDestination()
    blocker = BlockingRecordDestination("spanEnd")
    tracer = Tracer(origin="actant.test", destinations=(collector, blocker))

    spanReady = threading.Event()
    endSpan = threading.Event()
    workerErrors: list[BaseException] = []
    closeErrors: list[BaseException] = []
    spanHolder = []

    def spanWorker() -> None:
        try:
            span = tracer.span().start()
            spanHolder.append(span)
            spanReady.set()

            if not endSpan.wait(timeout=5):
                raise RuntimeError(  # noqa: TRY301
                    "Timed out waiting to begin span completion.",
                )

            span.complete()
        except BaseException as err:  # noqa: BLE001
            workerErrors.append(err)

    def closeTracer() -> None:
        try:
            tracer.close()
        except BaseException as err:  # noqa: BLE001
            closeErrors.append(err)

    workerThread = threading.Thread(target=spanWorker)
    workerThread.start()

    assert spanReady.wait(timeout=5)
    endSpan.set()
    assert blocker.entered.wait(timeout=5)

    closeThread = threading.Thread(target=closeTracer)
    closeThread.start()

    blocker.release.set()

    workerThread.join(timeout=5)
    closeThread.join(timeout=5)

    assert not workerThread.is_alive()
    assert not closeThread.is_alive()
    assert workerErrors == []
    assert closeErrors == []
    assert len(spanHolder) == 1
    assert tracer.getActiveSpanCount() == 0

    span = spanHolder[0]
    relatedRecords = [
        record
        for record in collector.records
        if record.spanId == span.spanId
    ]

    assert [record.kind for record in relatedRecords] == [
        "spanStart",
        "spanEnd",
    ]
    assert all(
        record.type != "trace.span-abandoned"
        for record in relatedRecords
    )


def testSpanEndAfterCloseIsRejectedWithoutSecondTerminalEvidence() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))

    spanReady = threading.Event()
    attemptEnd = threading.Event()
    workerErrors: list[BaseException] = []
    spanHolder = []

    def spanWorker() -> None:
        span = tracer.span().start()
        spanHolder.append(span)
        spanReady.set()

        if not attemptEnd.wait(timeout=5):
            workerErrors.append(
                RuntimeError(
                    "Timed out waiting to attempt span completion.",
                ),
            )
            return

        try:
            span.complete()
        except BaseException as err:  # noqa: BLE001
            workerErrors.append(err)

    workerThread = threading.Thread(target=spanWorker)
    workerThread.start()

    assert spanReady.wait(timeout=5)

    tracer.close()
    attemptEnd.set()

    workerThread.join(timeout=5)

    assert not workerThread.is_alive()
    assert len(spanHolder) == 1
    assert len(workerErrors) == 1
    assert isinstance(workerErrors[0], TraceClosedError)
    assert tracer.getActiveSpanCount() == 0

    span = spanHolder[0]
    relatedRecords = [
        record
        for record in collector.records
        if record.spanId == span.spanId
    ]

    assert [record.kind for record in relatedRecords] == [
        "spanStart",
        "event",
    ]
    assert relatedRecords[-1].type == "trace.span-abandoned"


def testConcurrentClosePublishesAbandonmentExactlyOnce() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    span = tracer.span().start()  # noqa: F841

    threadCount = 8
    barrier = threading.Barrier(threadCount)
    errors: list[BaseException] = []
    errorsLock = threading.Lock()

    def closeTracer() -> None:
        try:
            barrier.wait()
            tracer.close()
        except BaseException as err:  # noqa: BLE001
            with errorsLock:
                errors.append(err)

    threads = [
        threading.Thread(target=closeTracer)
        for _ in range(threadCount)
    ]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert tracer.getActiveSpanCount() == 0

    abandonmentRecords = [
        record
        for record in collector.records
        if record.type == "trace.span-abandoned"
    ]

    assert len(abandonmentRecords) == 1


def testCloseFallsBackToNonRestoringAbandonmentAfterLeaseRestoreFailure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emergencyStream = StringIO()
    collector = CollectingDestination()
    tracer = Tracer(
        origin="actant.test",
        destinations=(collector,),
        emergencyReporter=TraceEmergencyReporter(stream=emergencyStream),
    )
    span = tracer.span().start()
    spanContext = span.getContext()

    def failRestore(_self: object) -> None:
        raise TraceContextError("intentional context restoration failure")

    monkeypatch.setattr(
        "backend.tracing.context._ContextLease.restore",
        failRestore,
    )

    tracer.close()

    relatedRecords = [
        record
        for record in collector.records
        if record.spanId == span.spanId
    ]

    assert tracer.getActiveSpanCount() == 0
    assert tracer._getCurrentSpanContext() == spanContext  # noqa: SLF001
    assert [record.kind for record in relatedRecords] == [
        "spanStart",
        "event",
    ]
    assert relatedRecords[-1].type == "trace.span-abandoned"
    assert "intentional context restoration failure" in (
        emergencyStream.getvalue()
    )

    with pytest.raises(TraceClosedError):
        tracer.event().emit()
