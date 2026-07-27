# file: backend/core/dictpath.py ; version: 4
from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import cast

from backend.core.validation import requireExactNonBlankString, typeName

__all__: list[str] = [
    "deleteByPath",
    "getByPath",
    "hasPath",
    "setByPath",
    "splitPath",
]


_MISSING: object = object()


def splitPath(path: str) -> tuple[str, ...]:
    """
    Splits a dictionary path into unescaped segments.

    Both "." and "/" separate path segments. A backslash escapes the
    following character.

    Raises:
        TypeError:
            If path is not a string.
        ValueError:
            If path is blank, contains an empty segment, or ends with a
            dangling escape.

    """
    text = requireExactNonBlankString(path, "path")

    parts: list[str] = []
    current: list[str] = []
    escaped = False

    for char in text:
        if escaped:
            current.append(char)
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if char in (".", "/"):
            parts.append("".join(current))
            current = []
            continue

        current.append(char)

    if escaped:
        raise ValueError("path must not end with a dangling escape.")

    parts.append("".join(current))

    if any(part == "" for part in parts):
        raise ValueError(f"path contains an empty segment: {path!r}.")

    return tuple(parts)


def getByPath[D](
    value: object,
    path: str,
    default: D | None = None,
) -> object | D | None:
    """Returns the value at path, or default if path does not exist."""
    current = value

    for part in splitPath(path):
        if not isinstance(current, Mapping):
            return default

        mapping = cast(Mapping[str, object], current)

        if part not in mapping:
            return default

        current = mapping[part]

    return current


def setByPath(
    value: object,
    path: str,
    newValue: object,
    *,
    createIfMissing: bool = False,
) -> None:
    """
    Sets a value at path inside nested mutable mappings.

    Raises:
        TypeError:
            If a required path parent is not a mutable mapping.
        KeyError:
            If a path segment does not exist and createIfMissing is False.
        ValueError:
            If path syntax is invalid.

    """
    parts = splitPath(path)
    current = value

    for part in parts[:-1]:
        if not isinstance(current, MutableMapping):
            raise TypeError(
                "path parent must be a mutable mapping, "
                f"not {typeName(current)}.",
            )

        mapping = cast(MutableMapping[str, object], current)

        if part not in mapping:
            if not createIfMissing:
                raise KeyError(part)

            mapping[part] = {}

        current = mapping[part]

    if not isinstance(current, MutableMapping):
        raise TypeError(
            "path parent must be a mutable mapping, "
            f"not {typeName(current)}.",
        )

    mapping = cast(MutableMapping[str, object], current)
    mapping[parts[-1]] = newValue


def hasPath(value: object, path: str) -> bool:
    """Returns whether path exists."""
    return getByPath(value, path, _MISSING) is not _MISSING


def deleteByPath(value: object, path: str) -> bool:
    """
    Delete the value at path.

    Returns true when a value was deleted, or false when the path does not
    exist.

    Raises:
        TypeError:
            If the path exists up to a final parent that is not a mutable
            mapping.
        ValueError:
            If path syntax is invalid.

    """
    parts = splitPath(path)
    current = value

    for part in parts[:-1]:
        if not isinstance(current, Mapping):
            return False

        mapping = cast(Mapping[str, object], current)

        if part not in mapping:
            return False

        current = mapping[part]

    if not isinstance(current, MutableMapping):
        raise TypeError(
            "path parent must be a mutable mapping, "
            f"not {typeName(current)}.",
        )

    mapping = cast(MutableMapping[str, object], current)
    finalPart = parts[-1]

    if finalPart not in mapping:
        return False

    del mapping[finalPart]
    return True
