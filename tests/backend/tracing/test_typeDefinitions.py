# file: tests/backend/tracing/test_typeDefinitions.py ; version: 3
from __future__ import annotations

import pytest

from backend.tracing import (
    TRACE_EVENT,
    TRACE_SPAN,
    TraceEventType,
    TraceGeneratedType,
    TraceSpanType,
    TraceTypeConflictError,
    TraceTypeDefinition,
)
from backend.tracing.typeRegistry import TraceTypeRegistry


def testDefaultDefinitionsUseEmptyDomain() -> None:
    assert TRACE_EVENT.getDefinition().domain == ""
    assert TRACE_SPAN.getDefinition().domain == ""


def testCanonicalIdIsStableAcrossCustomOutcomeMappingOrder() -> None:
    first = TraceSpanType(
        name="pipeline.run",
        customOutcomes={
            "superseded": TraceGeneratedType("superseded", "info"),
            "timed-out": TraceGeneratedType("timed-out", "warning"),
        },
    )
    second = TraceSpanType(
        name="pipeline.run",
        customOutcomes={
            "timed-out": TraceGeneratedType("timed-out", "warning"),
            "superseded": TraceGeneratedType("superseded", "info"),
        },
    )

    assert (
        first.getDefinition().traceTypeDefinitionId
        == second.getDefinition().traceTypeDefinitionId
    )

    assert (
        first.getDefinition().toCanonicalJson()
        == second.getDefinition().toCanonicalJson()
    )


def testEveryDefaultAndCustomMemberAffectsDefinitionId() -> None:
    base = TraceSpanType(name="pipeline.run")
    changedLevel = TraceSpanType(
        name="pipeline.run",
        failed=TraceGeneratedType(
            "failed",
            "error",
        ),  # Normally warning level.
    )
    changedLabel = TraceSpanType(
        name="pipeline.run",
        failed=TraceGeneratedType(
            "aborted",
            "warning",
        ), # Normally failed label.
    )
    changedOutcome = TraceSpanType(
        name="pipeline.run",
        customOutcomes={
            "superseded": TraceGeneratedType("superseded", "info"),
        },
    )

    identities = {
        base.getDefinition().traceTypeDefinitionId,
        changedLevel.getDefinition().traceTypeDefinitionId,
        changedLabel.getDefinition().traceTypeDefinitionId,
        changedOutcome.getDefinition().traceTypeDefinitionId,
    }
    assert len(identities) == 4  # noqa: PLR2004


def testEventGeneratedLabelMustEqualEventName() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        TraceEventType(
            name="pipeline.ready",
            event=TraceGeneratedType("pipeline.other", "info"),
        )


def testRegistryRejectsSameActiveNameWithDifferentDefinitionId() -> None:
    registry = TraceTypeRegistry()
    registry.register(TraceEventType(name="pipeline.ready").getDefinition())

    with pytest.raises(TraceTypeConflictError):
        registry.register(
            TraceEventType(
                name="pipeline.ready",
                event=TraceGeneratedType("pipeline.ready", "warning"), # makes different definition id
            ).getDefinition(),
        )


def testTraceTypeDefinitionRejectsNonStringDefinitionKind() -> None:
    source = TRACE_EVENT.getDefinition()

    with pytest.raises(
        TypeError,
        match="definitionKind must be an exact built-in string",
    ):
        TraceTypeDefinition(
            traceTypeDefinitionId=source.traceTypeDefinitionId,
            definitionKind=1,  # ty: ignore[invalid-argument-type]
            name=source.name,
            domain=source.domain,
            event=source.event,
            started=source.started,
            outcomes=source.outcomes,
        )


def testTraceEventTypeRejectsStringSubclassDomain() -> None:
    class StringSubclass(str):  # noqa: SLOT000
        pass

    with pytest.raises(
        TypeError,
        match="domain must be an exact built-in string",
    ):
        TraceEventType(
            name="test.event",
            domain=StringSubclass("test"),
        )


def testEventDefinitionRejectsSpanMetadata() -> None:
    eventDefinition = TRACE_EVENT.getDefinition()
    spanDefinition = TRACE_SPAN.getDefinition()

    with pytest.raises(
        ValueError,
        match="Event definition must not contain started metadata",
    ):
        TraceTypeDefinition(
            traceTypeDefinitionId=eventDefinition.traceTypeDefinitionId,
            definitionKind="event",
            name=eventDefinition.name,
            domain=eventDefinition.domain,
            event=eventDefinition.event,
            started=spanDefinition.started,
            outcomes={},
        )


def testSpanDefinitionRejectsEventMetadata() -> None:
    eventDefinition = TRACE_EVENT.getDefinition()
    spanDefinition = TRACE_SPAN.getDefinition()

    with pytest.raises(
        ValueError,
        match="Span definition must not contain event metadata",
    ):
        TraceTypeDefinition(
            traceTypeDefinitionId=spanDefinition.traceTypeDefinitionId,
            definitionKind="span",
            name=spanDefinition.name,
            domain=spanDefinition.domain,
            event=eventDefinition.event,
            started=spanDefinition.started,
            outcomes={},
        )


def testSpanDefinitionRequiresAllStandardOutcomes() -> None:
    source = TRACE_SPAN.getDefinition()
    outcomes = dict(source.outcomes)
    del outcomes["cancelled"]

    with pytest.raises(
        ValueError,
        match="Span definition is missing standard outcomes: cancelled",
    ):
        TraceTypeDefinition(
            traceTypeDefinitionId=source.traceTypeDefinitionId,
            definitionKind="span",
            name=source.name,
            domain=source.domain,
            event=None,
            started=source.started,
            outcomes=outcomes,
        )
