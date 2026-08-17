# file: tests/backend/tracing/test_traceValidation.py ; version: 1
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from backend.tracing import TRACE_LEVELS, TRACE_RECORD_KINDS
from backend.tracing.validation import (
    requireDisplayName,
    requireName,
    requireOrigin,
    requireOutcomeName,
    requireTraceLevel,
    requireTraceRecordKind,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class StringSubclass(str):  # noqa: SLOT000
    pass


@pytest.mark.parametrize(
    "value",
    [
        "trace",
        "trace.event",
        "pipeline.run-1",
        "a_b.c+d",
        "a$b#c@d&e!f+g-h_i",
        "123",
    ],
)
def testRequireNameAcceptsCanonicalTracingNames(value: str) -> None:
    assert requireName(value, "value") is value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "Trace",
        ".trace",
        "trace.",
        "trace..event",
        "trace event",
        " trace",
        "trace ",
        "trace/event",
        "trace:event",
    ],
)
def testRequireNameRejectsInvalidTracingNames(value: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        requireName(value, "value")


@pytest.mark.parametrize(
    "value",
    [
        "completed",
        "timed-out",
        "custom_outcome",
        "a$b#c@d&e!f+g-h_i",
        "123",
    ],
)
def testRequireOutcomeNameAcceptsSingleNameSegment(value: str) -> None:
    assert requireOutcomeName(value) is value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "Completed",
        "span.completed",
        ".completed",
        "completed.",
        "timed out",
        " completed",
        "completed ",
    ],
)
def testRequireOutcomeNameRejectsInvalidOutcomeNames(value: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        requireOutcomeName(value)


@pytest.mark.parametrize("value", TRACE_LEVELS)
def testRequireTraceLevelAcceptsEveryCanonicalLevel(value: str) -> None:
    assert requireTraceLevel(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "INFO",
        "warn",
        "critical",
        " info",
        "info ",
    ],
)
def testRequireTraceLevelRejectsNonCanonicalLevel(value: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        requireTraceLevel(value)


@pytest.mark.parametrize("value", TRACE_RECORD_KINDS)
def testRequireTraceRecordKindAcceptsEveryCanonicalKind(value: str) -> None:
    assert requireTraceRecordKind(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "spanstart",
        "spanend",
        "span_start",
        "Event",
        " event",
        "event ",
    ],
)
def testRequireTraceRecordKindRejectsNonCanonicalKind(value: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        requireTraceRecordKind(value)


def testRequireOriginUsesTracingNameSyntax() -> None:
    assert requireOrigin("actant.runtime.worker") == "actant.runtime.worker"

    with pytest.raises(ValueError):  # noqa: PT011
        requireOrigin("Actant Runtime")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "Human readable text",
        "Pipeline ready!",
        " text with surrounding spaces ",
        "Žluťoučký kůň úpěl ďábelské ódy",
        "line one\nline two",
    ],
)
def testRequireDisplayNamePreservesArbitraryExactString(
    value: str,
) -> None:
    assert requireDisplayName(value) is value


@pytest.mark.parametrize(
    "validator",
    [
        requireName,
        requireOutcomeName,
        requireOrigin,
        requireDisplayName,
        requireTraceLevel,
        requireTraceRecordKind,
    ],
)
def testTracingStringValidatorsRejectStringSubclass(
    validator: Callable[..., object],
) -> None:
    with pytest.raises(TypeError):
        validator(StringSubclass("info"))
