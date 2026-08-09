# file: backend/core/validation.py ; version: 12
from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import PurePosixPath

__all__: list[str] = [
    "requireBool",
    "requireExactNonBlankString",
    "requireFiniteFloat",
    "requireFloat",
    "requireInstance",
    "requireInteger",
    "requireMapping",
    "requireNonBlankString",
    "requireNonNegativeInteger",
    "requireOptionalExactNonBlankString",
    "requireOptionalInstance",
    "requireOptionalNonNegativeInteger",
    "requireOptionalPositiveInteger",
    "requirePositiveFiniteFloat",
    "requirePositiveFloat",
    "requirePositiveInteger",
    "requireRelativePurePosixPath",
    "requireString",
    "typeName",
]


def _typeNameFromType(valueType: type[object]) -> str:
    """
    Returns a readable runtime type name for diagnostics.

    Built-in types use only their qualified name. Other types include their
    defining module to avoid ambiguous diagnostics.
    """

    if valueType.__module__ == "builtins":
        return valueType.__qualname__

    return f"{valueType.__module__}.{valueType.__qualname__}"


def typeName(value: object) -> str:
    """
    Returns a readable runtime type name for diagnostics.

    The name is derived from type(value). Built-in types use only their
    qualified name. Other types include their defining module to avoid
    ambiguous diagnostics.
    """
    return _typeNameFromType(type(value))


def _requireValidDiagnosticName(name: str) -> str:
    """
    Validates that name is a valid diagnostic name.

    This private validator cannot use the public string validators because
    those validators must validate their own diagnostic names.

    Raises:
        TypeError:
            If name is not an exact built-in string.
        ValueError:
            If name is blank or contains surrounding whitespace.

    """
    if type(name) is not str:
        raise TypeError(
            "name must be an exact built-in string; "
            f"received {typeName(name)}.",
        )

    if not name.strip():
        raise ValueError("name must not be blank.")

    if name != name.strip():
        raise ValueError(
            "name must not contain leading or trailing whitespace.",
        )

    return name


def _requireExpectedType[T](
    expectedType: type[T],
) -> type[T]:
    """
    Validates that expectedType is a runtime type.

    Raises:
        TypeError:
            If expectedType is not a runtime type.

    """
    if not isinstance(expectedType, type):
        raise TypeError(
            "expectedType must be a type; "
            f"received {typeName(expectedType)}.",
        )

    return expectedType


def requireString(value: str, name: str) -> str:
    """
    Validates that value is an exact built-in Python string.

    Raises:
        TypeError:
            If value is not an exact built-in string, or name is not an
            exact built-in string.
        ValueError:
            If name is blank or contains surrounding whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)

    if type(value) is not str:
        raise TypeError(
            f"{cleanName} must be an exact built-in string; "
            f"received {typeName(value)}.",
        )

    return value


def requireNonBlankString(value: str, name: str) -> str:
    """
    Validates that value is an exact built-in string containing
    non-whitespace content.

    Surrounding whitespace is permitted. Use requireExactNonBlankString when
    the supplied value must not contain leading or trailing whitespace.

    Raises:
        TypeError:
            If value is not an exact built-in string, or name is not an
            exact built-in string.
        ValueError:
            If value is blank or name is blank or contains surrounding
            whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)
    string = requireString(value, cleanName)

    if not string.strip():
        raise ValueError(f"{cleanName} must not be blank.")

    return string


def requireExactNonBlankString(value: str, name: str) -> str:
    """
    Validates that value is an exact nonblank built-in string without
    surrounding whitespace.

    The value is not normalized. Leading or trailing whitespace is rejected
    so the returned string is exactly the string supplied by the caller.

    Raises:
        TypeError:
            If value is not an exact built-in string, or name is not an
            exact built-in string.
        ValueError:
            If value is blank, contains surrounding whitespace, or name is
            blank or contains surrounding whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)
    string = requireNonBlankString(value, cleanName)

    if string != string.strip():
        raise ValueError(
            f"{cleanName} must not contain leading or trailing whitespace.",
        )

    return string


def requireMapping[K, V](
    value: Mapping[K, V],
    name: str,
) -> Mapping[K, V]:
    """
    Validates that value is a mapping while preserving its key and value
    types.

    Raises:
        TypeError:
            If value does not implement the Mapping protocol, or name
            is not an exact built-in string.
        ValueError:
            If name is blank or contains surrounding whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)

    if not isinstance(value, Mapping):
        raise TypeError(
            f"{cleanName} must be a mapping; received {typeName(value)}.",
        )

    return value


def requireOptionalExactNonBlankString(
    value: str | None,
    name: str,
) -> str | None:
    """
    Validates that value is None or an exact nonblank string without
    surrounding whitespace.

    Raises:
        TypeError:
            If value is neither None nor an exact built-in string, or name
            is not an exact built-in string.
        ValueError:
            If value is blank, contains surrounding whitespace, or name is
            blank or contains surrounding whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)
    if value is None:
        return None

    return requireExactNonBlankString(value, cleanName)


def requireFloat(value: float | int, name: str) -> float:  # noqa: PYI041
    """
    Validates that value is an exact built-in int or float and returns it
    as a float.

    Boolean values and subclasses of int or float are rejected. NaN is
    rejected because it does not represent an ordered numeric value.
    Positive and negative infinity are permitted.

    Raises:
        TypeError:
            If value is not an exact built-in int or float, or name is not
            an exact built-in string.
        ValueError:
            If value is NaN, cannot be represented as a float, or name is
            blank or contains surrounding whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)

    if type(value) not in (int, float):
        raise TypeError(
            f"{cleanName} must be an exact built-in int or float; "
            f"received {typeName(value)}.",
        )

    try:
        number = float(value)
    except OverflowError as err:
        raise ValueError(
            f"{cleanName} must be representable as a float.",
        ) from err

    if math.isnan(number):
        raise ValueError(f"{cleanName} must not be NaN.")

    return number


def requireFiniteFloat(
    value: float | int,  # noqa: PYI041
    name: str,
) -> float:
    """
    Validates that value is a finite exact built-in int or float and
    returns it as a float.

    Boolean values, subclasses of int or float, NaN, and positive or negative
    infinity are rejected.

    Raises:
        TypeError:
            If value is not an exact built-in int or float, or name is not
            an exact built-in string.
        ValueError:
            If value is not finite, cannot be represented as a float, or
            name is blank or contains surrounding whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)
    number = requireFloat(value, cleanName)

    if not math.isfinite(number):
        raise ValueError(f"{cleanName} must be finite.")

    return number


def requirePositiveFloat(
    value: float | int,  # noqa: PYI041
    name: str,
) -> float:
    """
    Validates that value is a positive exact built-in int or float and
    returns it as a float.

    NaN is rejected. Positive infinity is accepted because it is greater than
    zero. Negative infinity is rejected because it is less than zero.

    Raises:
        TypeError:
            If value is not an exact built-in int or float, or name is not
            an exact built-in string.
        ValueError:
            If value is not greater than zero, cannot be represented as a
            float, or name is blank or contains surrounding whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)
    number = requireFloat(value, cleanName)

    if number <= 0:
        raise ValueError(f"{cleanName} must be greater than zero.")

    return number


def requirePositiveFiniteFloat(
    value: float | int,  # noqa: PYI041
    name: str,
) -> float:
    """
    Validates that value is a positive finite exact built-in int or float
    and returns it as a float.

    Boolean values, subclasses of int or float, NaN, both infinities, zero,
    and negative values are rejected.

    Raises:
        TypeError:
            If value is not an exact built-in int or float, or name is not
            an exact built-in string.
        ValueError:
            If value cannot be represented as a float, is not finite, is
            not greater than zero, or name is blank or contains surrounding
            whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)
    number = requireFiniteFloat(value, cleanName)

    if number <= 0:
        raise ValueError(f"{cleanName} must be greater than zero.")

    return number


def requireInteger(value: int, name: str) -> int:
    """
    Validates that value is an exact built-in Python integer.

    Boolean values and subclasses of int are rejected.

    Raises:
        TypeError:
            If value is not an exact built-in integer, or name is
            not an exact built-in string.
        ValueError:
            If name is blank or contains surrounding whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)

    if type(value) is not int:
        raise TypeError(
            f"{cleanName} must be an exact built-in integer; "
            f"received {typeName(value)}.",
        )

    return value


def requirePositiveInteger(value: int, name: str) -> int:
    """
    Validates that value is an exact built-in Python integer greater than
    zero.

    Raises:
        TypeError:
            If value is not an exact built-in integer, or name is
            not an exact built-in string.
        ValueError:
            If value is not greater than zero or name is blank or
            contains surrounding whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)

    cleanValue = requireInteger(value, cleanName)

    if cleanValue <= 0:
        raise ValueError(f"{cleanName} must be greater than zero.")

    return cleanValue


def requireNonNegativeInteger(value: int, name: str) -> int:
    """
    Validates that value is an exact built-in Python integer greater than
    or equal to zero.

    Raises:
        TypeError:
            If value is not an exact built-in integer, or name is
            not an exact built-in string.
        ValueError:
            If value is negative or name is blank or contains surrounding
            whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)

    cleanValue = requireInteger(value, cleanName)

    if cleanValue < 0:
        raise ValueError(f"{cleanName} must not be negative.")

    return cleanValue


def requireOptionalPositiveInteger(value: int | None, name: str) -> int | None:
    """
    Validates that value is None or an exact built-in integer greater
    than zero.

    Raises:
        TypeError:
            If value is neither None nor an exact built-in integer, or
            name is not an exact built-in string.
        ValueError:
            If value is not greater than zero or name is blank or
            contains surrounding whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)

    if value is None:
        return None

    return requirePositiveInteger(value, cleanName)


def requireOptionalNonNegativeInteger(
    value: int | None,
    name: str,
) -> int | None:
    """
    Validates that value is None or an exact built-in integer greater
    than or equal to zero.

    Raises:
        TypeError:
            If value is neither None nor an exact built-in integer, or
            name is not an exact built-in string.
        ValueError:
            If value is negative or name is blank or contains surrounding
            whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)

    if value is None:
        return None

    return requireNonNegativeInteger(value, cleanName)


def requireBool(value: bool, name: str) -> bool:  # noqa: FBT001
    """
    Validates that value is an exact built-in Python bool.

    All values other than exact built-in bool values are rejected.

    Raises:
        TypeError:
            If value is not an exact built-in bool, or name is not an
            exact built-in string.
        ValueError:
            If name is blank or contains surrounding whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)

    if type(value) is not bool:
        raise TypeError(
            f"{cleanName} must be an exact built-in bool; "
            f"received {typeName(value)}.",
        )

    return value


def requireRelativePurePosixPath(value: str, name: str) -> PurePosixPath:
    """
    Validates that value identifies a non-empty relative POSIX path that
    remains inside its root.

    This validator rejects absolute paths, parent traversal, backslashes, and
    values that normalize to the current directory. It does not require fully
    canonical authored path syntax; harmless normalization such as ``a/./b``
    remains accepted.

    Raises:
        TypeError:
            If value is not an exact built-in string, or name is not an
            exact built-in string.
        ValueError:
            If value is blank, contains surrounding whitespace, uses a
            backslash, is absolute, identifies the current directory,
            contains parent traversal, or name is blank or contains
            surrounding whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)
    string = requireExactNonBlankString(value, cleanName)

    if "\\" in string:
        raise ValueError(f"{cleanName} must use '/' as its path separator.")

    path = PurePosixPath(string)

    if path.is_absolute():
        raise ValueError(f"{cleanName} must be a relative POSIX path.")

    if path == PurePosixPath("."):
        raise ValueError(f"{cleanName} must identify a path.")

    if any(part == ".." for part in path.parts):
        raise ValueError(
            f"{cleanName} must not traverse outside its containing root.",
        )

    return path


def requireInstance[T](
    value: object,
    expectedType: type[T],
    name: str,
) -> T:
    """
    Validates that value is an instance of expectedType.

    This validator accepts an arbitrary object for runtime classification.
    Subclasses of expectedType are accepted according to isinstance semantics.

    Raises:
        TypeError:
            If expectedType is not a type, value is not an instance of it,
            or name is not an exact built-in string.
        ValueError:
            If name is blank or contains surrounding whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)
    cleanExpectedType = _requireExpectedType(expectedType)

    if not isinstance(value, cleanExpectedType):
        raise TypeError(
            f"{cleanName} must be an instance of "
            f"{_typeNameFromType(cleanExpectedType)}; "
            f"received {typeName(value)}.",
        )

    return value


def requireOptionalInstance[T](
    value: object,
    expectedType: type[T],
    name: str,
) -> T | None:
    """
    Validates that value is None or an instance of expectedType.

    This validator accepts an arbitrary object for runtime classification.
    Subclasses of expectedType are accepted according to isinstance semantics.

    Raises:
        TypeError:
            If expectedType is not a type, value is neither None nor an
            instance of it, or name is not an exact built-in string.
        ValueError:
            If name is blank or contains surrounding whitespace.

    """
    cleanName = _requireValidDiagnosticName(name)
    cleanExpectedType = _requireExpectedType(expectedType)

    if value is None:
        return None

    return requireInstance(value, cleanExpectedType, cleanName)
