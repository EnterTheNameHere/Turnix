# file: tests/backend/tracing/test_runtimeContext.py ; version: 2
from __future__ import annotations

import asyncio
import threading
from io import StringIO

import pytest

from backend.core.ids import Uuid7Id
from backend.tracing import (
    TraceContextError,
    TraceCorrelationContext,
    TraceCorrelationScope,
    TraceEmergencyReporter,
    Tracer,
    TraceRuntimeContext,
    TraceSinkDestination,
)
from tests.backend.tracing.helpers import CollectingDestination


class ActantRunId(Uuid7Id):
    __slots__ = ()


class ApplicationId(Uuid7Id):
    __slots__ = ()


class ApplicationRunId(Uuid7Id):
    __slots__ = ()


def testSharedRuntimeContextCorrelationsAreObservedByMultipleTracers() -> None:
    runtimeContext = TraceRuntimeContext()
    firstCollector = CollectingDestination()
    secondCollector = CollectingDestination()

    firstTracer = Tracer(
        origin="actant.first",
        runtimeContext=runtimeContext,
        destinations=(firstCollector,),
    )
    secondTracer = Tracer(
        origin="actant.second",
        runtimeContext=runtimeContext,
        destinations=(secondCollector,),
    )
    applicationId = ApplicationId.new()

    with firstTracer.correlations(applicationId=applicationId):
        firstTracer.event().emit()
        secondTracer.event().emit()

    assert firstCollector.records[-1].applicationId == applicationId
    assert secondCollector.records[-1].applicationId == applicationId


def testCorrelationScopeRemainsUsableAfterCreatingTracerCloses() -> None:
    runtimeContext = TraceRuntimeContext()
    sink = TraceSinkDestination()
    emergencyStream = StringIO()
    firstTracer = Tracer(
        origin="actant.first",
        destinations=(sink,),
        runtimeContext=runtimeContext,
        emergencyReporter=TraceEmergencyReporter(stream=emergencyStream),
    )
    collector = CollectingDestination()
    secondTracer = Tracer(
        origin="actant.second",
        runtimeContext=runtimeContext,
        destinations=(collector,),
    )
    applicationId = ApplicationId.new()

    scope = firstTracer.correlations(applicationId=applicationId)
    firstTracer.close()

    with scope:
        secondTracer.event().emit()

    assert collector.records[-1].applicationId == applicationId


def testSharedRuntimeContextDoesNotMakeAmbientSpanCrossProducer() -> None:
    runtimeContext = TraceRuntimeContext()
    sink = TraceSinkDestination()
    firstTracer = Tracer(
        origin="actant.first",
        destinations=(sink,),
        runtimeContext=runtimeContext,
    )
    secondTracer = Tracer(
        origin="actant.second",
        destinations=(sink,),
        runtimeContext=runtimeContext,
    )

    span = firstTracer.span().start()

    with pytest.raises(TraceContextError):
        secondTracer.event().emit()

    span.complete()


def testNestedCorrelationScopesInheritClearAndRestoreExactValues() -> None:
    runtimeContext = TraceRuntimeContext()
    actantRunId = ActantRunId.new()
    applicationId = ApplicationId.new()
    applicationRunId = ApplicationRunId.new()

    outer = TraceCorrelationScope(
        runtimeContext=runtimeContext,
        actantRunId=actantRunId,
        applicationId=applicationId,
    )

    with outer:
        assert runtimeContext.getCurrentCorrelations() == (
            TraceCorrelationContext(
                actantRunId=actantRunId,
                applicationId=applicationId,
            )
        )

        with TraceCorrelationScope(
            runtimeContext=runtimeContext,
            applicationId=None,
            applicationRunId=applicationRunId,
        ):
            assert runtimeContext.getCurrentCorrelations() == (
                TraceCorrelationContext(
                    actantRunId=actantRunId,
                    applicationId=None,
                    applicationRunId=applicationRunId,
                )
            )

        assert runtimeContext.getCurrentCorrelations() == (
            TraceCorrelationContext(
                actantRunId=actantRunId,
                applicationId=applicationId,
            )
        )

    assert runtimeContext.getCurrentCorrelations() == TraceCorrelationContext()


def testCorrelationScopeCanBeReusedAfterSuccessfulExit() -> None:
    runtimeContext = TraceRuntimeContext()
    applicationId = ApplicationId.new()
    scope = TraceCorrelationScope(
        runtimeContext=runtimeContext,
        applicationId=applicationId,
    )

    with scope:
        assert (
            runtimeContext.getCurrentCorrelations().applicationId
            == applicationId
        )

    assert runtimeContext.getCurrentCorrelations().applicationId is None

    with scope:
        assert (
            runtimeContext.getCurrentCorrelations().applicationId
            == applicationId
        )

    assert runtimeContext.getCurrentCorrelations().applicationId is None


def testCorrelationScopeCannotBeReenteredWhileActive() -> None:
    runtimeContext = TraceRuntimeContext()
    scope = TraceCorrelationScope(runtimeContext=runtimeContext)

    with scope, pytest.raises(TraceContextError):
        scope.__enter__()


def testCorrelationScopeCannotRestoreFromAnotherAsyncioTask() -> None:
    async def run() -> None:
        runtimeContext = TraceRuntimeContext()
        applicationId = ApplicationId.new()
        scope = TraceCorrelationScope(
            runtimeContext=runtimeContext,
            applicationId=applicationId,
        )

        scope.__enter__()

        async def restoreFromChildTask() -> None:
            with pytest.raises(TraceContextError):
                scope.__exit__(None, None, None)

        await asyncio.create_task(restoreFromChildTask())

        assert (
            runtimeContext.getCurrentCorrelations().applicationId
            == applicationId
        )

        scope.__exit__(None, None, None)

        assert runtimeContext.getCurrentCorrelations().applicationId is None

    asyncio.run(run())


def testCorrelationScopeCannotRestoreFromAnotherThread() -> None:
    runtimeContext = TraceRuntimeContext()
    applicationId = ApplicationId.new()
    scope = TraceCorrelationScope(
        runtimeContext=runtimeContext,
        applicationId=applicationId,
    )
    errors: list[BaseException] = []

    scope.__enter__()

    def restoreFromThread() -> None:
        try:
            scope.__exit__(None, None, None)
        except BaseException as err:  # noqa: BLE001
            errors.append(err)

    thread = threading.Thread(target=restoreFromThread)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], TraceContextError)
    assert (
        runtimeContext.getCurrentCorrelations().applicationId
        == applicationId
    )

    scope.__exit__(None, None, None)

    assert runtimeContext.getCurrentCorrelations().applicationId is None


def testNewThreadObservesEmptyDefaultCorrelationContext() -> None:
    runtimeContext = TraceRuntimeContext()
    observed: list[TraceCorrelationContext] = []

    thread = threading.Thread(
        target=lambda: observed.append(
            runtimeContext.getCurrentCorrelations(),
        ),
    )
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert observed == [TraceCorrelationContext()]
