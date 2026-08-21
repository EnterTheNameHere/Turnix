# file: tests/backend/values/test_valuesValidation.py ; version: 1
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from backend.values import (
    requireValueAddress,
    requireValueAddressSegment,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class StringSubclass(str):  # noqa: SLOT000
    pass


def testRequireValueAddressReturnsOriginalStringUnchanged() -> None:
    value = "world/towns/prague"

    result = requireValueAddress(value, "address")

    assert result is value


def testRequireValueAddressRejectsStringSubclass() -> None:
    with pytest.raises(TypeError):
        requireValueAddress(StringSubclass("world/towns/prague"), "address")


def testRequireValueAddressReportsFullAddressForInvalidSegment() -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        requireValueAddress("world/Towns/prague", "address")

    message = str(err.value)

    assert "address segment 2" in message
    assert "must start with a lowercase ASCII letter or digit" in message
    assert "received: 'world/Towns/prague'" in message
    assert "segment: 'Towns'" in message
    assert "invalid first character: 'T'" in message


def testRequireValueAddressReportsInvalidCharacterInsideSegment() -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        requireValueAddress("world/toWns/prague", "address")

    message = str(err.value)

    assert "address segment 2 contains invalid character 'W'" in message
    assert "received: 'world/toWns/prague'" in message
    assert "segment: 'toWns'" in message


def testRequireValueAddressExplainsDoubleSeparator() -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        requireValueAddress("world//towns", "address")

    message = str(err.value)

    assert "empty segment at position 2" in message
    assert "received: 'world//towns'" in message
    assert "'//' was intentional" in message
    assert "single '/'" in message


def testRequireValueAddressReportsUnicodeCharacter() -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        requireValueAddress("world/město", "address")

    message = str(err.value)

    assert "ASCII characters only" in message
    assert "received: 'world/město'" in message
    assert "invalid character: 'ě'" in message


def testRequireValueAddressPreservesDiagnosticName() -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        requireValueAddress("world/Town", "sourceAddress")

    assert "sourceAddress segment 2" in str(err.value)


@pytest.mark.parametrize(
    ("validator", "value"),
    [
        (requireValueAddress, ""),
        (requireValueAddressSegment, ""),
    ],
)
def testValueAddressValidatorsRejectEmptyString(
    validator: Callable[[str, str], str],
    value: str,
) -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        validator(value, "address")

    message = str(err.value)

    assert "received: ''" in message
    assert "received: ''''" not in message


@pytest.mark.parametrize(
    "segment",
    [
        "world",
        "town-17",
        "town_17",
        "current.state",
        "0",
        "17-town",
    ],
)
def testRequireValueAddressSegmentAcceptsCanonicalSegment(
    segment: str,
) -> None:
    assert requireValueAddressSegment(segment, "segment") is segment


def testRequireValueAddressSegmentReportsInvalidCharacter() -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        requireValueAddressSegment("toWns", "segment")

    message = str(err.value)

    assert "segment contains invalid character 'W'" in message
    assert "received: 'toWns'" in message
    assert "ValueAddress segments allow only" in message


def testRequireValueAddressSegmentReportsInvalidFirstCharacter() -> None:
    with pytest.raises(ValueError) as err:  # noqa: PT011
        requireValueAddressSegment("-town", "segment")

    message = str(err.value)

    assert "must start with a lowercase ASCII letter or digit" in message
    assert "received: '-town'" in message
    assert "invalid first character: '-'" in message
