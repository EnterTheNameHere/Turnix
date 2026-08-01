# file: backend/core/validation.py ; version: 4
from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import cast

__all__: list[str] = [
    "requireBool",
    "requireExactNonBlankString",
    "requireFloat",
    "requireInstance",
    "requireInteger",
    "requireMapping",
    "requireNonBlankString",
    "requireOptionalExactNonBlankString",
    "requireOptionalInstance",
    "requireOptionalPositiveInteger",
    "requirePositiveFloat",
    "requireRelativePurePosixPath",
    "requireString",
    "typeName",
]


def typeName(value: object) -> str:
    """
    Returns a readable runtime type name for diagnostics.

    Built-in types use only their qualified name. Other types include their
    defining module to avoid ambiguous diagnostics.
    """
    valueType = type(value)

    if valueType.__module__ == "builtins":
        return valueType.__qualname__

    return f"{valueType.__module__}.{valueType.__qualname__}"


def requireString(
    value: object,
    name: str,
) -> str:
    """
    Require a Python string.

    Raises:
        TypeError:
            If value is not a string.

    """
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string, not {typeName(value)}.")

    return value


def requireNonBlankString(value: object, name: str) -> str:
    """
    Require a string containing at least one non-whitespace character.

    Raises:
        TypeError:
            If value is not a string.
        ValueError:
            If value is blank.

    """
    string = requireString(value, name)

    if not string.strip():
        raise ValueError(f"{name} must not be blank.")

    return string


def requireExactNonBlankString(value: object, name: str) -> str:
    """
    Require a non-blank string without surrounding whitespace.

    The value is not normalized. Leading or trailing whitespace is rejected
    so the returned string is exactly the string supplied by the caller.

    Raises:
        TypeError:
            If value is not a string.
        ValueError:
            If value is blank or contains leading or trailing whitespace.

    """
    string = requireNonBlankString(value, name)

    if string != string.strip():
        raise ValueError(
            f"{name} must not contain leading or trailing whitespace.",
        )

    return string


def requireMapping(value: object, name: str) -> Mapping[object, object]:
    """
    Require a mapping value.

    Raises:
        TypeError:
            If value does not implement the Mapping protocol.

    """
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping, not {typeName(value)}.")

    return cast(Mapping[object, object], value)


def requireOptionalExactNonBlankString(value: object, name: str) -> str | None:
    """
    Require either None or a non-blank string without surrounding whitespace.

    Raises:
        TypeError:
            If value is neither None nor a string.
        ValueError:
            If value is blank or contains leading or trailing whitespace.

    """
    if value is None:
        return None

    return requireExactNonBlankString(value, name)


def requireFloat(value: object, name: str) -> float:
    """
    Require an int or float and return it as a float.

    Boolean values are rejected even though bool is a subclass of int.

    Raises:
        TypeError:
            If value is not an int or float, or is a boolean.

    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"{name} must be an int or float, not {typeName(value)}.",
        )

    return float(value)


def requirePositiveFloat(value: object, name: str) -> float:
    """
    Require a positive numeric value and return it as a float.

    Raises:
        TypeError:
            If value is not an int or float, or is a boolean.
        ValueError:
            If value is not greater than zero.

    """
    number = requireFloat(value, name)

    if number <= 0:
        raise ValueError(f"{name} must be greater than zero.")

    return number


def requireInteger(value: object, name: str) -> int:
    """
    Require a Python integer while rejecting bool.

    Python bool is a subclass of int, but boolean values are not accepted as
    integers by this validator.

    Raises:
        TypeError:
            If value is not an integer or is a boolean.

    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, not {typeName(value)}.")

    return value


def requireOptionalPositiveInteger(value: object, name: str) -> int | None:
    """
    Require either None or an integer greater than zero.

    Raises:
        TypeError:
            If value is neither None nor an integer, or is a boolean.
        ValueError:
            If value is not greater than zero.

    """
    if value is None:
        return None

    cleanValue = requireInteger(value, name)

    if cleanValue < 1:
        raise ValueError(f"{name} must be greater than zero.")

    return cleanValue


def requireBool(value: object, name: str) -> bool:
    """
    Require a Python bool.

    Raises:
        TypeError:
            If value is not a bool.

    """
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool, not {typeName(value)}.")

    return value


def requireRelativePurePosixPath(value: object, name: str) -> PurePosixPath:
    """
    Require a non-empty relative POSIX path that remains inside its root.

    This validator rejects absolute paths, parent traversal, backslashes, and
    values that normalize to the current directory. It does not require fully
    canonical authored path syntax; harmless normalization such as ``a/./b``
    remains accepted.

    Raises:
        TypeError:
            If value is not a string.
        ValueError:
            if value is blank, contains surrounding whitespace, uses a
            backslash, is absolute, identifies the current directory, or
            contains parent traversal.

    """
    string = requireExactNonBlankString(value, name)

    if "\\" in string:
        raise ValueError(f"{name} must use '/' as its path separator.")

    path = PurePosixPath(string)

    if path.is_absolute():
        raise ValueError(f"{name} must be a relative POSIX path.")

    if path == PurePosixPath("."):
        raise ValueError(f"{name} must identify a path.")

    if any(part == ".." for part in path.parts):
        raise ValueError(
            f"{name} must not traverse outside its containing root.",
        )

    return path


def requireInstance[T](
    value: object,
    expectedType: type[T],
    name: str,
) -> T:
    """Returns value if it is an instance of the expected type."""
    cleanName = requireExactNonBlankString(name, "name")

    if not isinstance(value, expectedType):
        raise TypeError(
            f"{cleanName} must be an instance of {expectedType.__name__}; "
            f"got {typeName(value)}.",
        )

    return value


def requireOptionalInstance[T](
    value: object,
    expectedType: type[T],
    name: str,
) -> T | None:
    """Returns None or value if it is an instance of the expected type."""
    if value is None:
        return None

    return requireInstance(value, expectedType, name)
