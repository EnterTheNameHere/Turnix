# file: tests/backend/tracing/test_traceIds.py ; version: 1
from __future__ import annotations

import hashlib

import pytest

from backend.tracing import TraceTypeDefinitionId


@pytest.mark.parametrize(
    "value",
    [
        "sha256:" + ("0" * 64),
        "sha256:" + ("0123456789abcdef" * 4),
        "sha256:" + ("f" * 64),
    ],
)
def testTraceTypeDefinitionIdAcceptsCanonicalShape(value: str) -> None:
    definitionId = TraceTypeDefinitionId(value)

    assert definitionId.value == value
    assert str(definitionId) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "sha256:",
        "sha256:" + ("0" * 63),
        "sha256:" + ("0" * 65),
        "sha256:" + ("A" * 64),
        "SHA256:" + ("0" * 64),
        "md5:" + ("0" * 64),
        ("0" * 64),
        " sha256:" + ("0" * 64),
        "sha256:" + ("0" * 64) + " ",
        "sha256:" + ("g" * 64),
    ],
)
def testTraceTypeDefinitionIdRejectsInvalidCanonicalShape(value: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        TraceTypeDefinitionId(value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        1,
        True,
        b"sha256:",
    ],
)
def testTraceTypeDefinitionIdRejectsNonStringValue(value: object) -> None:
    with pytest.raises(
        TypeError,
        match="value must be an exact built-in string",
    ):
        TraceTypeDefinitionId(value)  # ty: ignore[invalid-argument-type]


def testTraceTypeDefinitionIdFromCanonicalJsonUsesExactUtf8Sha256() -> None:
    canonicalJson = '{"domain":"","name":"pipeline.ready"}'

    definitionId = TraceTypeDefinitionId.fromCanonicalJson(canonicalJson)

    expectedDigest = hashlib.sha256(canonicalJson.encode("utf-8")).hexdigest()

    assert definitionId == TraceTypeDefinitionId(f"sha256:{expectedDigest}")


def testTraceTypeDefinitionIdChangesWhenCanonicalJsonBytesChange() -> None:
    first = TraceTypeDefinitionId.fromCanonicalJson(
        '{"a":1, "b":2}',
    )
    second = TraceTypeDefinitionId.fromCanonicalJson(
        '{"b":2,"a":1}',
    )

    assert first != second
