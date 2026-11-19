# file: tests/backend/tracing/test_referenceBoundaries.py ; version: 1
from __future__ import annotations

import pytest

from backend.tracing.references import normalizeTraceReferences


def testReferenceNormalizerRequiresExactOuterTuple() -> None:
    with pytest.raises(TypeError, match="values must be an exact built-in tuple"):
        normalizeTraceReferences([("job", "job-1")])  # ty: ignore[invalid-argument-type]


def testReferenceNormalizerStillDeduplicatesNormalizedTupleInputs() -> None:
    normalized = normalizeTraceReferences(
        (
            ("job", "job-1"),
            ("job", "job-1"),
            ("job", "job-2"),
        ),
    )

    assert [(reference.kind, reference.id) for reference in normalized] == [
        ("job", "job-1"),
        ("job", "job-2"),
    ]
