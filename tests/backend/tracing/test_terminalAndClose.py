# file: tests/backend/tracing/test_terminalAndClose.py ; version: 3
from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

from backend.tracing import (
    TerminalTraceDestination,
    TraceEmergencyReporter,
    TraceEventType,
    TraceGeneratedType,
    Tracer,
)
from tests.backend.tracing.helpers import CollectingDestination

if TYPE_CHECKING:
    import pytest


class FailOnWriteStream(StringIO):
    def __init__(self) -> None:
        super().__init__()
        self._failOnWrite: int | None = None
        self.writeCount = 0

    def arm(self, failOnWrite: int) -> None:
        self._failOnWrite = self.writeCount + failOnWrite

    def write(self, value: str) -> int:
        self.writeCount += 1

        if self.writeCount == self._failOnWrite:
            raise OSError("intentional stream write failure")

        return super().write(value)


def testTerminalShowsTypeHumanLabelAndNoIds() -> None:
    stream = StringIO()
    terminal = TerminalTraceDestination(
        stream=stream,
        colorMode="never",
    )

    tracer = Tracer(origin="actant.test", destinations=(terminal,))
    eventType = TraceEventType(
        name="pipeline.ready",
        domain="pipeline",
        event=TraceGeneratedType(
            "pipeline.ready",
            "info",
            "Pipeline ready",
        ),
    )

    tracer.event(eventType).message("ready").emit()

    output = stream.getvalue()
    assert "pipeline.ready" in output
    assert "Pipeline ready" in output
    assert "ready" in output
    assert "traceEvent" not in output
    assert "traceSpan" not in output


def testTerminalMarksUnknownParentWithoutInventingNesting() -> None:
    stream = StringIO()
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    span = tracer.span().start()
    context = span.getContext()
    span.complete()

    terminal = TerminalTraceDestination(stream=stream, colorMode="never")
    tracer.addDestination(terminal)
    tracer.event().span(context).emit()

    assert "[unknown parent]" in stream.getvalue()


def testClosePublishesAbandonmentButNotInventedSpanEnd() -> None:
    collector = CollectingDestination()
    tracer = Tracer(origin="actant.test", destinations=(collector,))
    span = tracer.span().start()
    spanId = span.spanId

    tracer.close()

    related = [
        record
        for record in collector.records
        if record.spanId == spanId
    ]
    assert [record.kind for record in related] == ["spanStart", "event"]
    assert related[-1].type == "trace.span-abandoned"
    assert tracer.getActiveSpanCount() == 0


def testTerminalShowsSpanDuration() -> None:
    stream = StringIO()
    terminal = TerminalTraceDestination(stream=stream, colorMode="never")
    tracer = Tracer(origin="actant.test", destinations=(terminal,))

    tracer.span().start().complete()

    assert " ms]" in stream.getvalue() or " µs]" in stream.getvalue()


def testDefaultLabelCannotSpoofTerminalAbandonmentBookkeeping() -> None:
    stream = StringIO()
    terminal = TerminalTraceDestination(stream=stream, colorMode="never")
    tracer = Tracer(origin="actant.test", destinations=(terminal,))

    span = tracer.span().start()

    tracer.event().label("trace.span-abandoned").emit()
    tracer.event().message("still nested").emit()
    span.complete()

    lines = [
        line
        for line in stream.getvalue().splitlines()
        if "still nested" in line
    ]
    assert len(lines) == 1
    assert "[unknown parent]" not in lines[0]


def testTerminalDoesNotMislabelCustomOutcomeUsingStandardTypeText() -> None:
    stream = StringIO()
    terminal = TerminalTraceDestination(stream=stream, colorMode="never")
    tracer = Tracer(origin="actant.test", destinations=(terminal,))

    tracer.span().start().end("custom", label="completed")

    endLine = stream.getvalue().splitlines()[-1]
    assert "trace.span.completed" in endLine
    assert "- Completed" not in endLine


def testTerminalPrimaryStartFailureDoesNotCommitDisplayNesting() -> None:
    emergencyStream = StringIO()
    stream = FailOnWriteStream()
    terminal = TerminalTraceDestination(
        stream=stream,
        colorMode="never",
        showAttributes=False,
        showExceptions=False,
    )
    tracer = Tracer(
        origin="actant.test",
        destinations=(terminal,),
        emergencyReporter=TraceEmergencyReporter(stream=emergencyStream),
    )

    stream.arm(1)
    span = tracer.span().start()
    tracer.event().message("after failed start").emit()

    output = stream.getvalue()

    assert "after failed start" in output
    assert "[unknown parent]" in output
    assert "DESTINATION FAILURE" in emergencyStream.getvalue()

    span.complete()


def testTerminalEndCommitsDisplayCleanupBeforeDetailFailure() -> None:
    emergencyStream = StringIO()
    stream = FailOnWriteStream()
    terminal = TerminalTraceDestination(
        stream=stream,
        colorMode="never",
        showAttributes=True,
        showExceptions=False,
    )
    tracer = Tracer(
        origin="actant.test",
        destinations=(terminal,),
        emergencyReporter=TraceEmergencyReporter(stream=emergencyStream),
    )

    span = tracer.span().start()
    spanContext = span.getContext()

    stream.arm(2)
    span.complete(
        attributes={
            "detailed": "fails after primary line",
        },
    )

    tracer.emitEvent(
        span=spanContext,
        message="late evidence",
    )

    output = stream.getvalue()

    assert "late evidence" in output
    assert "[unknown parent]" in output
    assert "DESTINATION FAILURE" in emergencyStream.getvalue()


def testTerminalTimestampFormattingFallsBackToExactUnixNanoseconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingDateTime:
        @staticmethod
        def fromtimestamp(*args: object, **kwargs: object) -> object:  # noqa: ARG004
            raise OverflowError("intentional timestamp conversion failure")

    stream = StringIO()
    collector = CollectingDestination()
    terminal = TerminalTraceDestination(
        stream=stream,
        colorMode="never",
        showAttributes=False,
        showExceptions=False,
    )
    tracer = Tracer(
        origin="actant.test",
        destinations=(collector, terminal),
    )

    monkeypatch.setattr(
        "backend.tracing.terminalDestination.datetime.datetime",
        FailingDateTime,
    )

    tracer.event().message("fallback timestamp").emit()

    record = next(
        record
        for record in collector.records
        if record.message == "fallback timestamp"
    )

    assert f"[unix-ns:{record.timestampUnixNs}]" in stream.getvalue()
