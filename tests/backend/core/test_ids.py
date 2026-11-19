# file: tests/backend/core/test_ids.py ; version 1
from __future__ import annotations

import uuid

import pytest

from backend.core.ids import Uuid7Id, requireOptionalUuid7Id, requireUuid7Id


class ExampleId(Uuid7Id):
    __slots__ = ()


class OtherExampleId(Uuid7Id):
    __slots__ = ()


def testUuid7IdNewReturnsUuid7Id() -> None:
    value = Uuid7Id.new()

    assert isinstance(value, Uuid7Id)
    assert isinstance(value.value, uuid.UUID)
    assert value.value.version == Uuid7Id.UUID_VERSION
    assert str(value) == str(value.value)


def testUuid7IdNewPreservesConcreteSubclass() -> None:
    value = ExampleId.new()

    assert isinstance(value, ExampleId)
    assert type(value) is ExampleId
    assert value.value.version == Uuid7Id.UUID_VERSION


def testUuid7IdParseAcceptsCanonicalUuid7String() -> None:
    original = Uuid7Id.new()

    parsed = Uuid7Id.parse(str(original), "id")

    assert parsed == original
    assert type(parsed) is Uuid7Id


def testUuid7IdParsePreservesConcreteSubclass() -> None:
    original = ExampleId.new()

    parsed = ExampleId.parse(str(original), "id")

    assert parsed == original
    assert type(parsed) is ExampleId


def testUuid7IdParseRejectsBlankString() -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        Uuid7Id.parse("", "id")

    assert str(err.value) == "id must not be blank."


def testUuid7IdParseRejectsWhitespacePaddedString() -> None:
    value = str(Uuid7Id.new())

    with pytest.raises(ValueError) as err:  # noqa: PT011
        Uuid7Id.parse(f" {value}", "id")

    assert (
        str(err.value) == "id must not contain leading or trailing whitespace."
    )


def testUuid7IdParseRejectsUppercaseCanonicalText() -> None:
    value = str(Uuid7Id.new()).upper()

    with pytest.raises(ValueError) as err:  # noqa: PT011
        Uuid7Id.parse(value, "id")

    assert str(err.value) == "id must use canonical lowercase UUIDv7 syntax."


def testUuid7IdParseRejectsMalformedUuidString() -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        Uuid7Id.parse("not-a-uuid", "id")

    assert str(err.value) == "id must be a UUIDv7 string."


def testUuid7IdParseRejectsNonUuid7String() -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        Uuid7Id.parse(str(uuid.uuid4()), "id")

    assert str(err.value) == "id must be a UUIDv7."


def testUuid7IdParseValidatesDiagnosticName() -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        Uuid7Id.parse(str(Uuid7Id.new()), " id")

    assert (
        str(err.value)
        == "name must not contain leading or trailing whitespace."
    )


def testUuid7IdRejectsNonUuidValue() -> None:
    with pytest.raises(TypeError) as err:
        Uuid7Id("not-a-uuid")  # ty: ignore[invalid-argument-type]

    assert str(err.value) == "value must be a uuid.UUID, not str."


def testUuid7IdRejectsNonUuid7Value() -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        Uuid7Id(uuid.uuid4())

    assert str(err.value) == "value must be a UUIDv7."


def testDifferentUuid7IdDomainsAreNotEqual() -> None:
    value = uuid.uuid7()

    left = ExampleId(value)
    right = OtherExampleId(value)

    assert left != right


def testRequireUuid7IdAcceptsUuid7Id() -> None:
    value = Uuid7Id.new()

    result = requireUuid7Id(value, "id")

    assert result is value


def testRequireUuid7IdAcceptsSubclassAndPreservesType() -> None:
    value = ExampleId.new()

    result = requireUuid7Id(value, "id")

    assert result is value
    assert type(result) is ExampleId


def testRequireUuid7IdRejectsNone() -> None:
    with pytest.raises(TypeError) as err:
        requireUuid7Id(None, "id")  # ty: ignore[invalid-argument-type]

    assert (
        str(err.value) == "id must be a Uuid7Id identifier; received NoneType."
    )


def testRequireUuid7IdRejectsWrongValueType() -> None:
    with pytest.raises(TypeError) as err:
        requireUuid7Id("not-a-uuid", "id")  # ty: ignore[invalid-argument-type]

    assert str(err.value) == "id must be a Uuid7Id identifier; received str."


def testRequireUuid7IdValidatesDiagnosticName() -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        requireUuid7Id(Uuid7Id.new(), " id")

    assert (
        str(err.value)
        == "name must not contain leading or trailing whitespace."
    )


def testRequireOptionalUuid7IdReturnsNone() -> None:
    assert requireOptionalUuid7Id(None, "id") is None


def testRequireOptionalUuid7IdValidatesNameEvenWhenValueIsNone() -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        requireOptionalUuid7Id(None, " id")

    assert (
        str(err.value)
        == "name must not contain leading or trailing whitespace."
    )


def testRequireOptionalUuid7IdAcceptsUuid7Id() -> None:
    value = Uuid7Id.new()

    result = requireOptionalUuid7Id(value, "id")

    assert result is value


def testRequireOptionalUuid7IdAcceptsSubclassAndPreservesType() -> None:
    value = ExampleId.new()

    result = requireOptionalUuid7Id(value, "id")

    assert result is value
    assert type(result) is ExampleId


def testRequireOptionalUuid7IdRejectsWrongValueType() -> None:
    with pytest.raises(TypeError) as err:
        requireOptionalUuid7Id(
            "not-a-uuid",  # ty: ignore[invalid-argument-type]
            "id",
        )

    assert str(err.value) == "id must be a Uuid7Id identifier; received str."
