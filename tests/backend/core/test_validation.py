# file: tests/backend/core/test_validation.py ; version: 3
from __future__ import annotations

from collections import UserDict
from pathlib import PurePosixPath
from typing import Any

import pytest

from backend.core.validation import (
    requireBool,
    requireExactNonBlankString,
    requireFiniteFloat,
    requireFloat,
    requireInstance,
    requireInteger,
    requireMapping,
    requireNonBlankString,
    requireOptionalExactNonBlankString,
    requireOptionalInstance,
    requireOptionalNonNegativeInteger,
    requireOptionalPositiveInteger,
    requirePositiveFiniteFloat,
    requirePositiveFloat,
    requireRelativePurePosixPath,
    requireString,
    typeName,
)


class _StringSubclass(str):  # noqa: SLOT000
    pass


class _IntegerSubclass(int):
    pass


class _FloatSubclass(float):
    pass


class _ExampleBase:
    pass


class _ExampleChild(_ExampleBase):
    pass


class _CustomType:
    pass


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, type(None).__qualname__),
        (True, bool.__qualname__),
        (1, int.__qualname__),
        (1.5, float.__qualname__),
        ("value", str.__qualname__),
        ([], list.__qualname__),
        ({}, dict.__qualname__),
    ],
)
def testTypeNameReturnsBuiltInQualifiedName(
    value: object,
    expected: str,
) -> None:
    assert typeName(value) == expected


def testTypeNameIncludesModuleForNonBuiltInType() -> None:
    value = _CustomType()

    assert typeName(value) == (
        f"{_CustomType.__module__}.{_CustomType.__qualname__}"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "value",
        " value ",
        "\tvalue\n",
    ],
)
def testRequireStringReturnsExactBuiltInString(
    value: str,
) -> None:
    returned = requireString(value, "value")

    assert returned is value


@pytest.mark.parametrize(
    "value",
    [
        1,
        True,
        1.5,
        None,
        _StringSubclass("value"),
    ],
)
def testRequireStringRejectsInvalidType(
    value: object,
) -> None:
    with pytest.raises(
        TypeError,
        match=(
            r"value must be an exact built-in string; "
            r"received .+\."
        ),
    ):
        requireString(value, "value")  # ty:ignore[invalid-argument-type]


@pytest.mark.parametrize(
    "value",
    [
        "value",
        "value ",
        " value",
        "\tvalue\n",
    ],
)
def testRequireNonBlankStringReturnsNonBlankString(
    value: str,
) -> None:
    returned = requireNonBlankString(value, "value")

    assert returned is value


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "\t",
        "\n",
    ],
)
def testRequireNonBlankStringRejectsBlankString(
    value: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"value must not be blank\.",
    ):
        requireNonBlankString(value, "value")


@pytest.mark.parametrize(
    "value",
    [
        "value",
        "value.with.parts",
        "value[0]",
    ],
)
def testRequireExactNonBlankStringReturnsValidString(
    value: str,
) -> None:
    returned = requireExactNonBlankString(value, "value")

    assert returned is value


@pytest.mark.parametrize(
    "value",
    [
        " value",
        "value ",
        "\tvalue",
        "value\n",
    ],
)
def testRequireExactNonBlankStringRejectsSurroundingWhitespace(
    value: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"value must not contain leading or trailing whitespace\.",
    ):
        requireExactNonBlankString(value, "value")


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "\t",
        "\n",
    ],
)
def testRequireExactNonBlankStringRejectsBlankString(
    value: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"value must not be blank\.",
    ):
        requireExactNonBlankString(value, "value")


@pytest.mark.parametrize(
    "value",
    [
        None,
        "value",
    ],
)
def testRequireOptionalExactNonBlankStringReturnsSupportedValue(
    value: str | None,
) -> None:
    returned = requireOptionalExactNonBlankString(value, "value")

    assert returned is value


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", r"value must not be blank\."),
        (" value", r"value must not contain leading or trailing whitespace\."),
        ("value ", r"value must not contain leading or trailing whitespace\."),
    ],
)
def testRequireOptionalExactNonBlankStringRejectsInvalidString(
    value: str,
    message: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=message,
    ):
        requireOptionalExactNonBlankString(value, "value")


def testRequireOptionalExactNonBlankStringRejectsInvalidType() -> None:
    with pytest.raises(
        TypeError,
        match=(
            r"value must be an exact built-in string; received int\."
        ),
    ):
        requireOptionalExactNonBlankString(
            1,  # ty:ignore[invalid-argument-type]
            "value",
        )


def testRequireMappingReturnsSameMapping() -> None:
    value = {
        "first": 1,
        "second": 2,
    }

    returned = requireMapping(value, "value")

    assert returned is value


def testRequireMappingAcceptsMappingImplementation() -> None:
    value: UserDict[str, int] = UserDict(
        {
            "first": 1,
        },
    )

    returned = requireMapping(value, "value")

    assert returned is value


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        (),
        "value",
        1,
    ],
)
def testRequireMappingRejectsNonMapping(
    value: object,
) -> None:
    with pytest.raises(
        TypeError,
        match=r"value must be a mapping; received .+\.",
    ):
        requireMapping(value, "value")  # ty:ignore[invalid-argument-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0.0),
        (1, 1.0),
        (-1, -1.0),
        (0.0, 0.0),
        (1.5, 1.5),
        (-1.5, -1.5),
        (float("inf"), float("inf")),
        (float("-inf"), float("-inf")),
    ],
)
def testRequireFloatReturnsFloat(
    value: float | int,  # noqa: PYI041
    expected: float,
) -> None:
    returned = requireFloat(value, "value")

    assert returned == expected
    assert type(returned) is float


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "1",
        _IntegerSubclass(1),
        _FloatSubclass(1.0),
    ],
)
def testRequireFloatRejectsInvalidType(
    value: object,
) -> None:
    with pytest.raises(
        TypeError,
        match=(
            r"value must be an exact built-in int or float; "
            r"received .+\."
        ),
    ):
        requireFloat(value, "value")  # ty:ignore[invalid-argument-type]


def testRequireFloatRejectsNaN() -> None:
    with pytest.raises(
        ValueError,
        match=r"value must not be NaN\.",
    ):
        requireFloat(float("nan"), "value")


def testRequireFloatRejectsIntegerThatCannotBeRepresentedAsFloat() -> None:
    with pytest.raises(
        ValueError,
        match=r"value must be representable as a float\.",
    ):
        requireFloat(10**10000, "value")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0.0),
        (1, 1.0),
        (-1, -1.0),
        (1.5, 1.5),
        (-1.5, -1.5),
    ],
)
def testRequireFiniteFloatReturnsFiniteFloat(
    value: float | int,  # noqa: PYI041
    expected: float,
) -> None:
    returned = requireFiniteFloat(value, "value")

    assert returned == expected
    assert type(returned) is float


@pytest.mark.parametrize(
    "value",
    [
        float("inf"),
        float("-inf"),
    ],
)
def testRequireFiniteFloatRejectsInfinity(
    value: float,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"value must be finite\.",
    ):
        requireFiniteFloat(value, "value")


def testRequireFiniteFloatRejectsNaN() -> None:
    with pytest.raises(
        ValueError,
        match=r"value must not be NaN\.",
    ):
        requireFiniteFloat(float("nan"), "value")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1.0),
        (1.5, 1.5),
        (float("inf"), float("inf")),
    ],
)
def testRequirePositiveFloatReturnsPositiveFloat(
    value: float | int,  # noqa: PYI041
    expected: float,
) -> None:
    returned = requirePositiveFloat(value, "value")

    assert returned == expected
    assert type(returned) is float


@pytest.mark.parametrize(
    "value",
    [
        0,
        0.0,
        -1,
        -1.5,
        float("-inf"),
    ],
)
def testRequirePositiveFloatRejectsNonPositiveValue(
    value: float | int,  # noqa: PYI041
) -> None:
    with pytest.raises(
        ValueError,
        match=r"value must be greater than zero\.",
    ):
        requirePositiveFloat(value, "value")


def testRequirePositiveFloatRejectsNaN() -> None:
    with pytest.raises(
        ValueError,
        match=r"value must not be NaN\.",
    ):
        requirePositiveFloat(float("nan"), "value")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1.0),
        (1.5, 1.5),
    ],
)
def testRequirePositiveFiniteFloatReturnsSupportedValue(
    value: float | int,  # noqa: PYI041
    expected: float,
) -> None:
    returned = requirePositiveFiniteFloat(value, "value")

    assert returned == expected
    assert type(returned) is float


@pytest.mark.parametrize(
    "value",
    [
        0,
        0.0,
        -1,
        -1.5,
    ],
)
def testRequirePositiveFiniteFloatRejectsNonPositiveValue(
    value: float | int,  # noqa: PYI041
) -> None:
    with pytest.raises(
        ValueError,
        match=r"value must be greater than zero\.",
    ):
        requirePositiveFiniteFloat(value, "value")


@pytest.mark.parametrize(
    "value",
    [
        float("inf"),
        float("-inf"),
    ],
)
def testRequirePositiveFiniteFloatRejectsInfinity(
    value: float,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"value must be finite\.",
    ):
        requirePositiveFiniteFloat(value, "value")


def testRequirePositiveFiniteFloatRejectsNaN() -> None:
    with pytest.raises(
        ValueError,
        match=r"value must not be NaN\.",
    ):
        requirePositiveFiniteFloat(float("nan"), "value")


@pytest.mark.parametrize(
    "value",
    [
        0,
        1,
        -1,
    ],
)
def testRequireIntegerReturnsExactInteger(
    value: int,
) -> None:
    returned = requireInteger(value, "value")

    assert returned is value


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "1",
        1.0,
        _IntegerSubclass(1),
    ],
)
def testRequireIntegerRejectsInvalidType(
    value: object,
) -> None:
    with pytest.raises(
        TypeError,
        match=(
            r"value must be an exact built-in integer; "
            r"received .+\."
        ),
    ):
        requireInteger(value, "value")  # ty:ignore[invalid-argument-type]


@pytest.mark.parametrize(
    "value",
    [
        None,
        1,
        10,
    ],
)
def testRequireOptionalPositiveIntegerReturnsSupportedValue(
    value: int | None,
) -> None:
    returned = requireOptionalPositiveInteger(value, "value")

    assert returned is value


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
    ],
)
def testRequireOptionalPositiveIntegerRejectsNonPositiveValue(
    value: int,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"value must be greater than zero\.",
    ):
        requireOptionalPositiveInteger(value, "value")


@pytest.mark.parametrize(
    "value",
    [
        True,
        1.0,
        "1",
        _IntegerSubclass(1),
    ],
)
def testRequireOptionalPositiveIntegerRejectsInvalidType(
    value: object,
) -> None:
    with pytest.raises(
        TypeError,
        match=(
            r"value must be an exact built-in integer; received .+\."
        ),
    ):
        requireOptionalPositiveInteger(
            value,  # ty:ignore[invalid-argument-type]
            "value",
        )


@pytest.mark.parametrize(
    "value",
    [
        None,
        0,
        1,
        10,
    ],
)
def testRequireOptionalNonNegativeIntegerReturnsSupportedValue(
    value: int | None,
) -> None:
    returned = requireOptionalNonNegativeInteger(value, "value")

    assert returned is value


def testRequireOptionalNonNegativeIntegerRejectsNegativeValue() -> None:
    with pytest.raises(
        ValueError,
        match=r"value must not be negative\.",
    ):
        requireOptionalNonNegativeInteger(-1, "value")


@pytest.mark.parametrize(
    "value",
    [
        True,
        1.0,
        "1",
        _IntegerSubclass(1),
    ],
)
def testRequireOptionalNonNegativeIntegerRejectsInvalidType(
    value: object,
) -> None:
    with pytest.raises(
        TypeError,
        match=(
            r"value must be an exact built-in integer; received .+\."
        ),
    ):
        requireOptionalNonNegativeInteger(
            value,  # ty:ignore[invalid-argument-type]
            "value",
        )


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
    ],
)
def testRequireBoolReturnsExactBool(
    value: bool,  # noqa: FBT001
) -> None:
    returned = requireBool(value, "value")

    assert returned is value


@pytest.mark.parametrize(
    "value",
    [
        None,
        0,
        1,
        "true",
    ],
)
def testRequireBoolRejectsInvalidType(
    value: object,
) -> None:
    with pytest.raises(
        TypeError,
        match=(
            r"value must be an exact built-in bool; "
            r"received .+\."
        ),
    ):
        requireBool(value, "value")  # ty:ignore[invalid-argument-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("file.txt", PurePosixPath("file.txt")),
        ("directory/file.txt", PurePosixPath("directory/file.txt")),
        ("a/./b", PurePosixPath("a/b")),
        ("a//b", PurePosixPath("a/b")),
    ],
)
def testRequireRelativePurePosixPathReturnsPath(
    value: str,
    expected: PurePosixPath,
) -> None:
    assert requireRelativePurePosixPath(value, "value") == expected


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", r"value must not be blank\."),
        (" value", r"value must not contain leading or trailing whitespace\."),
        ("value ", r"value must not contain leading or trailing whitespace\."),
        (r"directory\file.txt", r"value must use '/' as its path separator\."),
        ("/directory/file.txt", r"value must be a relative POSIX path\."),
        (".", r"value must identify a path\."),
        ("a/../b", r"value must not traverse outside its containing root\."),
        (
            "../file.txt",
            r"value must not traverse outside its containing root\.",
        ),
    ],
)
def testRequireRelativePurePosixPathRejectsInvalidPath(
    value: str,
    message: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=message,
    ):
        requireRelativePurePosixPath(value, "value")


def testRequireRelativePurePosixPathRejectsInvalidType() -> None:
    with pytest.raises(
        TypeError,
        match=r"value must be an exact built-in string; received int\.",
    ):
        requireRelativePurePosixPath(
            1,  # ty:ignore[invalid-argument-type]
            "value",
        )


def testRequireInstanceReturnsExactInstance() -> None:
    value = _ExampleBase()

    returned = requireInstance(value, _ExampleBase, "value")

    assert returned is value


def testRequireInstanceAcceptsSubclassInstance() -> None:
    value = _ExampleChild()

    returned = requireInstance(value, _ExampleBase, "value")

    assert returned is value


def testRequireInstanceRejectsWrongInstanceType() -> None:
    with pytest.raises(
        TypeError,
        match=(
            r"value must be an instance of _ExampleBase; "
            r"received str\."
        ),
    ):
        requireInstance("value", _ExampleBase, "value")


def testRequireInstanceRejectsInvalidExpectedType() -> None:
    with pytest.raises(
        TypeError,
        match=r"expectedType must be a type; received str\.",
    ):
        requireInstance(
            object(),
            "type",  # ty:ignore[invalid-argument-type]
            "value",
        )


def testRequireOptionalInstanceReturnsNone() -> None:
    assert requireOptionalInstance(None, _ExampleBase, "value") is None


def testRequireOptionalInstanceReturnsInstance() -> None:
    value = _ExampleChild()

    returned = requireOptionalInstance(value, _ExampleBase, "value")

    assert returned is value


def testRequireOptionalInstanceRejectsWrongInstanceType() -> None:
    with pytest.raises(
        TypeError,
        match=(
            r"value must be an instance of _ExampleBase; "
            r"received str\."
        ),
    ):
        requireOptionalInstance("value", _ExampleBase, "value")


def testRequireOptionalInstanceValidatesExpectedTypeForNone() -> None:
    with pytest.raises(
        TypeError,
        match=r"expectedType must be a type; received str\.",
    ):
        requireOptionalInstance(
            None,
            "type",  # ty:ignore[invalid-argument-type]
            "value",
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
def testPublicValidatorsRejectBlankDiagnosticName(
    name: str,
) -> None:
    validators = [
        lambda: requireString("value", name),
        lambda: requireNonBlankString("value", name),
        lambda: requireExactNonBlankString("value", name),
        lambda: requireMapping({}, name),
        lambda: requireOptionalExactNonBlankString(None, name),
        lambda: requireFloat(1, name),
        lambda: requireFiniteFloat(1, name),
        lambda: requirePositiveFloat(1, name),
        lambda: requirePositiveFiniteFloat(1, name),
        lambda: requireInteger(1, name),
        lambda: requireOptionalPositiveInteger(None, name),
        lambda: requireOptionalNonNegativeInteger(None, name),
        lambda: requireBool(True, name),  # noqa: FBT003
        lambda: requireRelativePurePosixPath("file.txt", name),
        lambda: requireInstance(object(), object, name),
        lambda: requireOptionalInstance(None, object, name),
    ]

    for validate in validators:
        with pytest.raises(
            ValueError,
            match=r"name must not be blank\.",
        ):
            validate()


@pytest.mark.parametrize(
    "name",
    [
        " value",
        "value ",
        "\tvalue",
        "value\n",
    ],
)
def testPublicValidatorsRejectDiagnosticNameWithWhitespace(
    name: str,
) -> None:
    validators = [
        lambda: requireString("value", name),
        lambda: requireNonBlankString("value", name),
        lambda: requireExactNonBlankString("value", name),
        lambda: requireMapping({}, name),
        lambda: requireOptionalExactNonBlankString(None, name),
        lambda: requireFloat(1, name),
        lambda: requireFiniteFloat(1, name),
        lambda: requirePositiveFloat(1, name),
        lambda: requirePositiveFiniteFloat(1, name),
        lambda: requireInteger(1, name),
        lambda: requireOptionalPositiveInteger(None, name),
        lambda: requireOptionalNonNegativeInteger(None, name),
        lambda: requireBool(True, name),  # noqa: FBT003
        lambda: requireRelativePurePosixPath("file.txt", name),
        lambda: requireInstance(object(), object, name),
        lambda: requireOptionalInstance(None, object, name),
    ]

    for validate in validators:
        with pytest.raises(
            ValueError,
            match=r"name must not contain leading or trailing whitespace\.",
        ):
            validate()


def testPublicValidatorsRejectNonStringDiagnosticName() -> None:
    name: Any = 7

    validators = [
        lambda: requireString("value", name),
        lambda: requireNonBlankString("value", name),
        lambda: requireExactNonBlankString("value", name),
        lambda: requireMapping({}, name),
        lambda: requireOptionalExactNonBlankString(None, name),
        lambda: requireFloat(1, name),
        lambda: requireFiniteFloat(1, name),
        lambda: requirePositiveFloat(1, name),
        lambda: requirePositiveFiniteFloat(1, name),
        lambda: requireInteger(1, name),
        lambda: requireOptionalPositiveInteger(None, name),
        lambda: requireOptionalNonNegativeInteger(None, name),
        lambda: requireBool(True, name),  # noqa: FBT003
        lambda: requireRelativePurePosixPath("file.txt", name),
        lambda: requireInstance(object(), object, name),
        lambda: requireOptionalInstance(None, object, name),
    ]

    for validate in validators:
        with pytest.raises(
            TypeError,
            match=r"name must be an exact built-in string; received int\.",
        ):
            validate()


def testPublicValidatorsRejectStringSubclassDiagnosticName() -> None:
    name: Any = _StringSubclass("value")

    validators = [
        lambda: requireString("value", name),
        lambda: requireNonBlankString("value", name),
        lambda: requireExactNonBlankString("value", name),
        lambda: requireMapping({}, name),
        lambda: requireOptionalExactNonBlankString(None, name),
        lambda: requireFloat(1, name),
        lambda: requireFiniteFloat(1, name),
        lambda: requirePositiveFloat(1, name),
        lambda: requirePositiveFiniteFloat(1, name),
        lambda: requireInteger(1, name),
        lambda: requireOptionalPositiveInteger(None, name),
        lambda: requireOptionalNonNegativeInteger(None, name),
        lambda: requireBool(True, name),  # noqa: FBT003
        lambda: requireRelativePurePosixPath("file.txt", name),
        lambda: requireInstance(object(), object, name),
        lambda: requireOptionalInstance(None, object, name),
    ]

    for validate in validators:
        with pytest.raises(
            TypeError,
            match=(
                r"name must be an exact built-in string; "
                r"received .*_StringSubclass\."
            ),
        ):
            validate()
