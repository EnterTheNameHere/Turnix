# file: tests/backend/core/test_immutableValue.py ; version: 2
from __future__ import annotations

from collections import UserDict, UserList
from collections.abc import Mapping
from typing import Any, cast

import pytest

from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer


class _StringSubclass(str):  # noqa: SLOT000
    pass


class _IntegerSubclass(int):
    pass


class _FloatSubclass(float):
    pass


class _CallableUserDict(UserDict[str, object]):
    def __call__(self) -> None:
        return None


class _CallableUserList(UserList[object]):
    def __call__(self) -> None:
        return None


@pytest.fixture
def freezer() -> ImmutableValueFreezer:
    return ImmutableValueFreezer()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (False, False),
        (True, True),
        (0, 0),
        (42, 42),
        (-42, -42),
        (0.0, 0.0),
        (3.5, 3.5),
        (-3.5, -3.5),
        ("", ""),
        ("Actant", "Actant"),
    ],
)
def testFreezeReturnsSupportedScalar(
    freezer: ImmutableValueFreezer,
    value: object,
    expected: ImmutableValue,
) -> None:
    assert freezer.freeze(value) == expected


def testFreezePreservesScalarRuntimeTypes(
    freezer: ImmutableValueFreezer,
) -> None:
    values = (
        None,
        False,
        1,
        1.5,
        "value",
    )

    frozen = tuple(freezer.freeze(value) for value in values)

    assert frozen[0] is None
    assert type(frozen[1]) is bool
    assert type(frozen[2]) is int
    assert type(frozen[3]) is float
    assert type(frozen[4]) is str


@pytest.mark.parametrize(
    "value",
    [
        (),
        [],
        UserList(),
    ],
)
def testFreezeConvertsSupportedEmptySequenceToTuple(
    freezer: ImmutableValueFreezer,
    value: object,
) -> None:
    frozen = freezer.freeze(value)

    assert frozen == ()
    assert type(frozen) is tuple


@pytest.mark.parametrize(
    "value",
    [
        (None, True, 7, 2.5, "text"),
        [None, True, 7, 2.5, "text"],
        UserList([None, True, 7, 2.5, "text"]),
    ],
)
def testFreezeConvertsSupportedSequenceToTuple(
    freezer: ImmutableValueFreezer,
    value: object,
) -> None:
    frozen = freezer.freeze(value)

    assert frozen == (None, True, 7, 2.5, "text")
    assert type(frozen) is tuple


@pytest.mark.parametrize(
    "value",
    [
        {},
        UserDict(),
    ],
)
def testFreezeConvertsSupportedEmptyMappingToImmutableMapping(
    freezer: ImmutableValueFreezer,
    value: object,
) -> None:
    frozen = freezer.freeze(value)

    assert isinstance(frozen, Mapping)
    assert frozen == {}


@pytest.mark.parametrize(
    "value",
    [
        {
            "none": None,
            "bool": True,
            "integer": 7,
            "float": 2.5,
            "string": "text",
        },
        UserDict(
            {
                "none": None,
                "bool": True,
                "integer": 7,
                "float": 2.5,
                "string": "text",
            },
        ),
    ],
)
def testFreezeConvertsSupportedMappingToImmutableMapping(
    freezer: ImmutableValueFreezer,
    value: object,
) -> None:
    frozen = freezer.freeze(value)

    assert isinstance(frozen, Mapping)
    assert frozen == {
        "none": None,
        "bool": True,
        "integer": 7,
        "float": 2.5,
        "string": "text",
    }


def testFreezeRecursivelyFreezesNestedSupportedValues(
    freezer: ImmutableValueFreezer,
) -> None:
    source = {
        "metadata": {
            "enabled": True,
            "score": 0.75,
        },
        "items": [
            {
                "name": "first",
                "values": [1, 2, 3],
            },
            {
                "name": "second",
                "value": (),
            },
        ],
    }

    frozen = cast(Mapping[object, object], freezer.freeze(source, "input"))

    assert frozen == {
        "metadata": {
            "enabled": True,
            "score": 0.75,
        },
        "items": (
            {
                "name": "first",
                "values": (1, 2, 3),
            },
            {
                "name": "second",
                "value": (),
            },
        ),
    }

    assert isinstance(frozen, Mapping)

    metadata = frozen["metadata"]
    assert isinstance(metadata, Mapping)

    items = frozen["items"]
    assert type(items) is tuple

    firstItem = cast(Mapping[object, object], items[0])
    assert isinstance(firstItem, Mapping)
    assert firstItem["values"] == (1, 2, 3)
    assert type(firstItem["values"]) is tuple


def testFreezeCreatesSnapshotIndependentFromSource(
    freezer: ImmutableValueFreezer,
) -> None:
    sourceItems = [1, 2]
    sourceMetadata = {"status": "original"}
    source = {
        "items": sourceItems,
        "metadata": sourceMetadata,
    }

    frozen = freezer.freeze(source)

    sourceItems.append(3)
    sourceMetadata["metadata"] = "changed"
    source["new"] = "value"

    assert frozen == {
        "items": (1, 2),
        "metadata": {"status": "original"},
    }


def testFreezeResultCannotBeMutated(
    freezer: ImmutableValueFreezer,
) -> None:
    frozen = cast(
        Any,
        freezer.freeze(
            {
                "outer": {
                    "value": 1,
                },
            },
        ),
    )

    assert isinstance(frozen, Mapping)

    with pytest.raises(TypeError):
        frozen["new"] = 2

    nested = frozen["outer"]
    assert isinstance(nested, Mapping)

    with pytest.raises(TypeError):
        nested["value"] = 2


def testFreezeAllowsRepeatedNonCyclicContainerReference(
    freezer: ImmutableValueFreezer,
) -> None:
    shared = {
        "value": [1, 2],
    }
    source = {
        "left": shared,
        "right": shared,
    }

    frozen = freezer.freeze(source)

    assert frozen == {
        "left": {
            "value": (1, 2),
        },
        "right": {
            "value": (1, 2),
        },
    }


def testFreezeMappingReturnsSharedEmptyMappingForNone(
    freezer: ImmutableValueFreezer,
) -> None:
    first = freezer.freezeMapping(None)
    second = freezer.freezeMapping(None)

    assert first == {}
    assert second == {}
    assert first is second


def testFreezeMappingFreezesSupportedMapping(
    freezer: ImmutableValueFreezer,
) -> None:
    frozen = freezer.freezeMapping(
        {
            "scalar": 1,
            "sequence": [2, 3],
            "mapping": {
                "nested": True,
            },
        },
        "attributes",
    )

    assert frozen == {
        "scalar": 1,
        "sequence": (2, 3),
        "mapping": {
            "nested": True,
        },
    }


def testFreezeMappingRejectsNonMapping(
    freezer: ImmutableValueFreezer,
) -> None:
    with pytest.raises(
        TypeError,
        match=r"attributes must be a mapping, not list\.",
    ):
        freezer.freezeMapping(
            [],  # ty:ignore[invalid-argument-type]
            "attributes",
        )


@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        "\t",
        "\n",
    ],
)
def testFreezeRejectsBlankDiagnosticName(
    freezer: ImmutableValueFreezer,
    name: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"name must not be blank\.",
    ):
        freezer.freeze({}, name)


def testFreezeRejectsNonStringDiagnosticName(
    freezer: ImmutableValueFreezer,
) -> None:
    with pytest.raises(
        TypeError,
        match=r"name must be an exact built-in string; received int\.",
    ):
        freezer.freeze({}, 7)  # ty:ignore[invalid-argument-type]


def testFreezeRejectsNonStringMappingKey(
    freezer: ImmutableValueFreezer,
) -> None:
    with pytest.raises(
        TypeError,
        match=(
            r"Mapping key at input must be an exact built-in string; "
            r"received int\."
        ),
    ):
        freezer.freeze(
            {
                1: "value",
            },
            "input",
        )


def testFreezeReportsUnambiguousNestedMappingPath(
    freezer: ImmutableValueFreezer,
) -> None:
    with pytest.raises(
        TypeError,
        match=(
            r"Unsupported value at "
            r"input\['request\.metadata'\]\['callback'\]: object\."
        ),
    ):
        freezer.freeze(
            {
                "request.metadata": {
                    "callback": object(),
                },
            },
            "input",
        )


@pytest.mark.parametrize(
    "value",
    [
        set(),
        {1, 2},
        frozenset(),
        frozenset({1, 2}),
    ],
)
def testFreezeRejectsSetValues(
    freezer: ImmutableValueFreezer,
    value: object,
) -> None:
    with pytest.raises(
        TypeError,
        match=(
            r"Value at input must not be a set. "
            r"Convert it to a deterministically ordered sequence\."
        ),
    ):
        freezer.freeze(value, "input")


def testFreezeRejectsCallable(
    freezer: ImmutableValueFreezer,
) -> None:
    def callback() -> None:
        return None

    with pytest.raises(
        TypeError,
        match=(
            r"Value at input\['callback'\] must not be a callable; "
            r"received .*function\."
        ),
    ):
        freezer.freeze(
            {
                "callback": callback,
            },
            "input",
        )


@pytest.mark.parametrize(
    "value",
    [
        b"bytes",
        bytearray(b"bytes"),
        object(),
    ],
)
def testFreezeRejectsUnsupportedValue(
    freezer: ImmutableValueFreezer,
    value: object,
) -> None:
    with pytest.raises(
        TypeError,
        match=r"Unsupported value at input:",
    ):
        freezer.freeze(value, "input")


def testFreezeRejectsDirectMappingCycle(
    freezer: ImmutableValueFreezer,
) -> None:
    source: dict[str, object] = {}
    source["self"] = source

    with pytest.raises(
        ValueError,
        match=(
            r"Cycle detected at input\['self'\]; "
            r"the same container is already active at input\."
        ),
    ):
        freezer.freeze(source, "input")


def testFreezeRejectsDirectSequenceCycle(
    freezer: ImmutableValueFreezer,
) -> None:
    source: list[object] = []
    source.append(source)

    with pytest.raises(
        ValueError,
        match=(
            r"Cycle detected at input\[0\]; "
            r"the same container is already active at input\."
        ),
    ):
        freezer.freeze(source, "input")


def testFreezeRejectsIndirectCycle(
    freezer: ImmutableValueFreezer,
) -> None:
    parent: dict[str, object] = {}
    child: list[object] = [parent]
    parent["child"] = child

    with pytest.raises(
        ValueError,
        match=(
            r"Cycle detected at input\['child'\]\[0\]; "
            r"the same container is already active at input\."
        ),
    ):
        freezer.freeze(parent, "input")


def testFreezeAllowsRootContainerAtMaxDepthZero() -> None:
    freezer = ImmutableValueFreezer(maxDepth=0)

    assert freezer.freeze({"value": 1}) == {"value": 1}
    assert freezer.freeze([1, 2]) == (1, 2)


@pytest.mark.parametrize(
    "value",
    [
        {
            "nested": {},
        },
        [
            [],
        ],
    ],
)
def testFreezeRejectsNestedContainerBeyondMaxDepthZero(
    value: object,
) -> None:
    freezer = ImmutableValueFreezer(maxDepth=0)

    with pytest.raises(
        ValueError,
        match=r"exceeds maxDepth=0; got depth 1\.",
    ):
        freezer.freeze(value, "input")


def testFreezeAllowsContainersAtConfiguredMaximumDepth() -> None:
    freezer = ImmutableValueFreezer(maxDepth=2)

    frozen = freezer.freeze(
        {
            "levelOne": [
                {
                    "value": 1,
                },
            ],
        },
    )

    assert frozen == {
        "levelOne": (
            {
                "value": 1,
            },
        ),
    }


def testFreezeRejectsContainerBeyondConfiguredMaximumDepth() -> None:
    freezer = ImmutableValueFreezer(maxDepth=1)

    with pytest.raises(
        ValueError,
        match=(
            r"Container at input\['levelOne'\]\[0\] "
            r"exceeds maxDepth=1; got depth 2\."
        ),
    ):
        freezer.freeze(
            {
                "levelOne": [
                    {},
                ],
            },
            "input",
        )


def testFreezeAllowsExactlyConfiguredMaximumItems() -> None:
    freezer = ImmutableValueFreezer(maxItems=4)

    frozen = freezer.freeze(
        {
            "first": [1, 2],
            "second": 3,
        },
    )

    assert frozen == {
        "first": (1, 2),
        "second": 3,
    }


def testFreezeRejectsMoreThanConfiguredMaximumItems() -> None:
    freezer = ImmutableValueFreezer(maxItems=3)

    with pytest.raises(
        ValueError,
        match=(
            r"Immutable value exceeds maxItems=3 "
            r"while traversing input\['second'\]\."
        ),
    ):
        freezer.freeze(
            {
                "first": [1, 2],
                "second": 3,
            },
            "input",
        )


def testFreezeAllowsEmptyContainersWhenMaxItemsIsZero() -> None:
    freezer = ImmutableValueFreezer(maxItems=0)

    assert freezer.freeze({}) == {}
    assert freezer.freeze([]) == ()


@pytest.mark.parametrize(
    "value",
    [
        {
            "value": 1,
        },
        [
            1,
        ],
    ],
)
def testFreezeRejectsAnyItemWhenMaxItemsIsZero(
    value: object,
) -> None:
    freezer = ImmutableValueFreezer(maxItems=0)

    with pytest.raises(
        ValueError,
        match=r"Immutable value exceeds maxItems=0",
    ):
        freezer.freeze(value)


def testFreezeAllowsExactlyConfiguredMaximumStringLength() -> None:
    freezer = ImmutableValueFreezer(maxStringLength=5)

    frozen = freezer.freeze(
        {
            "key": "value",
        },
    )

    assert frozen == {
        "key": "value",
    }


def testFreezeRejectsStringValueBeyondMaximumLength() -> None:
    freezer = ImmutableValueFreezer(maxStringLength=4)

    with pytest.raises(
        ValueError,
        match=(
            r"String at input\['key'\] exceeds "
            r"maxStringLength=4; got length 5\."
        ),
    ):
        freezer.freeze(
            {
                "key": "value",
            },
            "input",
        )


def testFreezeRejectsMappingKeyBeyondMaximumStringLength() -> None:
    freezer = ImmutableValueFreezer(maxStringLength=4)

    with pytest.raises(
        ValueError,
        match=(
            r"String at input\['longKey'\] mapping key exceeds "
            r"maxStringLength=4; got length 7\."
        ),
    ):
        freezer.freeze(
            {
                "longKey": 1,
            },
            "input",
        )


def testFreezeAllowsEmptyStringWhenMaximumStringLengthIsZero() -> None:
    freezer = ImmutableValueFreezer(maxStringLength=0)

    assert freezer.freeze("") == ""
    assert freezer.freeze({"": ""}) == {"": ""}


@pytest.mark.parametrize(
    ("argumentName", "arguments"),
    [
        ("maxDepth", {"maxDepth": -1}),
        ("maxItems", {"maxItems": -1}),
        ("maxStringLength", {"maxStringLength": -1}),
    ],
)
def testConstructorRejectsNegativeLimit(
    argumentName: str,
    arguments: dict[str, Any],
) -> None:
    with pytest.raises(
        ValueError,
        match=rf"{argumentName} must not be negative\.",
    ):
        ImmutableValueFreezer(**arguments)


@pytest.mark.parametrize(
    ("argumentName", "arguments"),
    [
        ("maxDepth", {"maxDepth": True}),
        ("maxItems", {"maxItems": 1.5}),
        ("maxStringLength", {"maxStringLength": "10"}),
    ],
)
def testConstructorRejectsInvalidLimitType(
    argumentName: str,
    arguments: dict[str, Any],
) -> None:
    with pytest.raises(
        TypeError,
        match=rf"{argumentName} must be an integer",
    ):
        ImmutableValueFreezer(**arguments)


@pytest.mark.parametrize(
    ("value", "typePattern"),
    [
        (_StringSubclass("value"), r".*\._StringSubclass"),
        (_IntegerSubclass(7), r".*\._IntegerSubclass"),
        (_FloatSubclass(2.5), r".*\._FloatSubclass"),
    ],
)
def testFreezeRejectsScalarSubclass(
    freezer: ImmutableValueFreezer,
    value: object,
    typePattern: str,
) -> None:
    with pytest.raises(
        TypeError,
        match=(
            r"Scalar value at input must use an exact built-in type; "
            rf"received {typePattern}"
        ),
    ):
        freezer.freeze(value, "input")


def testFreezeRejectsStringSubclassMappingKey(
    freezer: ImmutableValueFreezer,
) -> None:
    key = _StringSubclass("key")

    with pytest.raises(
        TypeError,
        match=(
            r"Mapping key at input must be an exact built-in string; "
            r"received .*_StringSubclass\."
        ),
    ):
        freezer.freeze(
            {
                key: "value",
            },
            "input",
        )


@pytest.mark.parametrize(
    ("value", "typePattern"),
    [
        (_CallableUserDict({"value": 1}), r".*\._CallableUserDict"),
        (_CallableUserList([1, 2]), r".*\._CallableUserList"),
    ],
)
def testFreezeRejectsCallableContainer(
    freezer: ImmutableValueFreezer,
    value: object,
    typePattern: str,
) -> None:
    with pytest.raises(
        TypeError,
        match=(
            r"Value at input must not be a callable; "
            rf"received {typePattern}"
        ),
    ):
        freezer.freeze(value, "input")


@pytest.mark.parametrize(
    "name",
    [
        " input",
        "input ",
        "\tinput",
        "input\n",
    ],
)
def testFreezeRejectsDiagnosticNameWithSurroundingWhitespace(
    freezer: ImmutableValueFreezer,
    name: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"name must not contain leading or trailing whitespace\.",
    ):
        freezer.freeze({}, name)


def testFreezeMappingRejectsInvalidDiagnosticName(
    freezer: ImmutableValueFreezer,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"name must not contain leading or trailing whitespace\.",
    ):
        freezer.freezeMapping({}, " attributes")


def testFreezeMappingResultCannotBeMutated(
    freezer: ImmutableValueFreezer,
) -> None:
    frozen = cast(Any, freezer.freezeMapping({"value": 1}))

    with pytest.raises(TypeError):
        frozen["value"] = 2


def testFreezeMappingSharedEmptyMappingCannotBeMutated(
    freezer: ImmutableValueFreezer,
) -> None:
    frozen = cast(Any, freezer.freezeMapping(None))

    with pytest.raises(TypeError):
        frozen["value"] = 1


def testFreezeItemCountResetsBetweenOperations() -> None:
    freezer = ImmutableValueFreezer(maxItems=1)

    assert freezer.freeze([1]) == (1,)
    assert freezer.freeze([2]) == (2,)


def testFreezeCountsRepeatedContainerAtEachLocation() -> None:
    shared = [1]
    freezer = ImmutableValueFreezer(maxItems=3)

    with pytest.raises(
        ValueError,
        match=r"Immutable value exceeds maxItems=3",
    ):
        freezer.freeze(
            {
                "left": shared,
                "right": shared,
            },
        )
