# file: tests/backend/values/test_valueAddress.py ; version: 1
from __future__ import annotations

import pytest

from backend.values import ValueAddress


@pytest.mark.parametrize(
    "text",
    [
        "world",
        "world/towns/prague",
        "npc/alice-17",
        "player/current_state",
        "packs/com.example.hospital",
        "location/bar_42/current.state",
        "0",
        "0/root",
    ],
)
def testValueAddressAcceptsCanonicalAddress(text: str) -> None:
    address = ValueAddress(text)

    assert address.value == text
    assert str(address) == text


@pytest.mark.parametrize(
    "text",
    [
        "World",
        "world/Towns/prague",
        "WORLD",
        "world/PRAGUE",
    ],
)
def testValueAddressRejectsUppercaseCharacters(text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        ValueAddress(text)


@pytest.mark.parametrize(
    "text",
    [
        "svět",
        "world/město",
        "npc/aлиса",  # noqa: RUF001
        "npc/アリス",
        "npc/😀",
    ],
)
def testValueAddressRejectsUnicode(text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        ValueAddress(text)


@pytest.mark.parametrize(
    "text",
    [
        "/world",
        "world/",
        "world//town",
        "world///town",
    ],
)
def testValueAddressRejectsInvalidSeparatorPlacement(text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        ValueAddress(text)


@pytest.mark.parametrize(
    "text",
    [
        "world\\town",
        "world town",
        "world\ttown",
        "world\ntown",
        "world\rtown",
        "world/*/town",
        "world/?/town",
        "world/[town]",
        "world/(town)",
        "world/{town}",
        "world/a,b",
        "world/a|b",
        "world/a:b",
        "world/a<b",
        "world/a>b",
    ],
)
def testValueAddressRejectsCharactersOutsideCanonicalGrammar(
    text: str,
) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        ValueAddress(text)


@pytest.mark.parametrize(
    "text",
    [
        ".world",
        "-world",
        "_world",
        "world/.town",
        "world/-town",
        "world/_town",
    ],
)
def testValueAddressSegmentMustStartWithLetterOrDigit(text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        ValueAddress(text)


def testValueAddressDoesNotNormalizeInput() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        ValueAddress("World/Towns/Prague")


def testValueAddressHasValueSemantics() -> None:
    first = ValueAddress("world/towns/prague")
    second = ValueAddress("world/towns/prague")
    different = ValueAddress("world/towns/brno")

    assert first == second
    assert first != different
    assert hash(first) == hash(second)


def testValueAddressIsImmutable() -> None:
    address = ValueAddress("world/towns/prague")

    with pytest.raises(AttributeError):
        address.value = "world/towns/brno"  # ty: ignore[invalid-assignment]


def testValueAddressRejectsNonStringValue() -> None:
    with pytest.raises(TypeError):
        ValueAddress(42)  # ty: ignore[invalid-argument-type]
