# file: backend/core/validation.py ; version: 6
from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath

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
    "requireOptionalNonNegativeInteger",
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


def requireString(value: str, name: str) -> str:
    """
    Require an exact built-in Python string.

    Raises:
        TypeError:
            If value is not an exact built-in string.

    """
    if type(value) is not str:
        raise TypeError(
            f"{name} must be an exact built-in string; "
            f"received {typeName(value)}.",
        )

    return value


def requireNonBlankString(value: str, name: str) -> str:
    """
    Require an exact built-in Python string containing non-whitespace content.

    Raises:
        TypeError:
            If value is not an exact built-in string.
        ValueError:
            If value is blank.

    """
    string = requireString(value, name)

    if not string.strip():
        raise ValueError(f"{name} must not be blank.")

    return string


def requireExactNonBlankString(value: str, name: str) -> str:
    """
    Require an exact built-in Python string without surrounding whitespace.

    The value is not normalized. Leading or trailing whitespace is rejected
    so the returned string is exactly the string supplied by the caller.

    Raises:
        TypeError:
            If value is not an exact built-in string.
        ValueError:
            If value is blank or contains leading or trailing whitespace.

    """
    string = requireNonBlankString(value, name)

    if string != string.strip():
        raise ValueError(
            f"{name} must not contain leading or trailing whitespace.",
        )

    return string


def requireMapping(
    value: Mapping[object, object],
    name: str,
) -> Mapping[object, object]:
    """
    Require a mapping value.

    Raises:
        TypeError:
            If value does not implement the Mapping protocol.

    """
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping, not {typeName(value)}.")

    return value


def requireOptionalExactNonBlankString(value: str | None, name: str) -> str | None:
    """
    Require either None or an exact built-in Python string without surrounding whitespace.

    Raises:
        TypeError:
            If value is neither None nor an exact built-in string.
        ValueError:
            If value is blank or contains leading or trailing whitespace.

    """
    if value is None:
        return None

    return requireExactNonBlankString(value, name)


def requireFloat(value: float | int, name: str) -> float:  # noqa: PYI041
    """
    Require an exact built-in Python int or float and return it as a float.

    Boolean values and subclasses of int or float are rejected.

    Raises:
        TypeError:
            If value is not an exact built-in int or float.

    """
    if type(value) not in (int, float):
        raise TypeError(
            f"{name} must be an exact built-in int or float; "
            f"received {typeName(value)}.",
        )

    return float(value)


def requirePositiveFloat(value: float | int, name: str) -> float:  # noqa: PYI041
    """
    Require a positive exact built-in Python int or float and return a float.

    Raises:
        TypeError:
            If value is not an exact built-in int or float.
        ValueError:
            If value is not greater than zero.

    """
    number = requireFloat(value, name)

    if number <= 0:
        raise ValueError(f"{name} must be greater than zero.")

    return number


def requireInteger(value: int, name: str) -> int:
    """
    Require an exact built-in Python integer.

    Boolean values and subclasses of int are rejected.

    Raises:
        TypeError:
            If value is not an exact built-in integer.

    """
    if type(value) is not int:
        raise TypeError(
            f"{name} must be an exact built-in integer; "
            f"received {typeName(value)}.",
        )

    return value


def requireOptionalPositiveInteger(value: int | None, name: str) -> int | None:
    """
    Require either None or an exact built-in Python integer greater than or equal to zero.

    Raises:
        TypeError:
            If value is neither None nor an exact built-in integer.
        ValueError:
            If value is not greater than zero.

    """
    if value is None:
        return None

    cleanValue = requireInteger(value, name)

    if cleanValue < 1:
        raise ValueError(f"{name} must be greater than zero.")

    return cleanValue


def requireOptionalNonNegativeInteger(value: int | None, name: str) -> int | None:
    """
    Require None or an exact built-in integer greater than or equal to zero.

    Raises:
        TypeError:
            If value is neither None nor an exact built-in integer.
        ValueError:
            If value is negative.

    """
    if value is None:
        return None

    cleanValue = requireInteger(value, name)

    if cleanValue < 0:
        raise ValueError(f"{name} must not be negative.")

    return cleanValue


def requireBool(value: bool, name: str) -> bool:  # noqa: FBT001
    """
    Require an exact built-in Python bool.

    Raises:
        TypeError:
            If value is not an exact built-in bool.

    """
    if type(value) is not bool:
        raise TypeError(
            f"{name} must be an exact built-in bool; "
            f"received {typeName(value)}.",
        )

    return value


def requireRelativePurePosixPath(value: str, name: str) -> PurePosixPath:
    """
    Require a non-empty relative POSIX path that remains inside its root.

    This validator rejects absolute paths, parent traversal, backslashes, and
    values that normalize to the current directory. It does not require fully
    canonical authored path syntax; harmless normalization such as ``a/./b``
    remains accepted.

    Raises:
        TypeError:
            If value is not an exact built-in string.
        ValueError:
            If value is blank, contains surrounding whitespace, uses a
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
