# file: backend/tracing/canonical.py ; version: 2
from __future__ import annotations

from collections.abc import Mapping, Sequence

from backend.core.validation import typeName

__all__: list[str] = [
    "canonicalJson",
]


_SURROGATE_CODE_POINT_MIN = 0xD800
_SURROGATE_CODE_POINT_MAX = 0xDFFF
_PRINTABLE_ASCII_MIN = 0x20
_PRINTABLE_ASCII_MAX = 0x7E
_BMP_CODE_POINT_MAX = 0xFFFF


def canonicalJson(value: object) -> str:
    """
    Returns Actant canonical JSON for deterministic tracing identities.

    The portable tracing profile accepts only null, exact booleans, exact
    integers, exact strings, non-string sequences, and mappings with exact
    string keys.

    Mapping keys are ordered lexicographically by Unicode code point.
    Strings use the shortest required JSON escapes, never escape '/', preserve
    all other Unicode as UTF-8 text, and reject lone surrogate code points.
    No whitespace is emitted outside strings.

    Raises:
        TypeError:
            If value contains an unsupported value type or a mapping key that
            is not an exact built-in string.
        ValueError:
            If a string value or mapping key contains a lone surrogate code
            point.

    """
    return _encode(value, path="$")


def _encode(
    value: object,
    *,
    path: str,
) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is str:
        return _encodeString(value, path=path)

    if isinstance(value, Mapping):
        items: list[tuple[str, object]] = []

        for rawKey, rawValue in value.items():
            if type(rawKey) is not str:
                raise TypeError(
                    f"Canonical JSON object key at {path} must be an exact "
                    f"string; received {typeName(rawKey)}.",
                )

            items.append((rawKey, rawValue))

        items.sort(key=lambda item: item[0])

        encoded = (
            _encodeString(
                key,
                path=_appendObjectKeyPath(path, key),
            )
            + ":"
            + _encode(
                rawValue,
                path=_appendObjectKeyPath(path, key),
            )
            for key, rawValue in items
        )
        return "{" + ",".join(encoded) + "}"

    if isinstance(value, Sequence) and not isinstance(
        value,
        str | bytes | bytearray,
    ):
        encoded = (
            _encode(
                item,
                path=f"{path}[{index}]",
            )
            for index, item in enumerate(value)
        )
        return "[" + ",".join(encoded) + "]"

    raise TypeError(
        f"Unsupported canonical JSON value at {path}: {typeName(value)}.",
    )


def _encodeString(
    value: str,
    *,
    path: str,
) -> str:
    parts: list[str] = ['"']
    shortEscapes = {
        0x08: "\\b",
        0x09: "\\t",
        0x0A: "\\n",
        0x0C: "\\f",
        0x0D: "\\r",
    }

    for character in value:
        codePoint = ord(character)

        if character == '"':
            parts.append('\\"')
        elif character == "\\":
            parts.append("\\\\")
        elif codePoint in shortEscapes:
            parts.append(shortEscapes[codePoint])
        elif codePoint < _PRINTABLE_ASCII_MIN:
            parts.append(f"\\u{codePoint:04x}")
        elif (
            _SURROGATE_CODE_POINT_MIN
            <= codePoint
            <= _SURROGATE_CODE_POINT_MAX
        ):
            raise ValueError(
                f"Canonical JSON string at {path} contains a lone surrogate.",
            )
        else:
            parts.append(character)

    parts.append('"')
    return "".join(parts)


def _appendObjectKeyPath(
    path: str,
    key: str,
) -> str:
    """
    Returns an unambiguous diagnostic path for one object key.

    The key is represented using ASCII-safe escaped text so malformed Unicode,
    including lone surrogate code points, can be named safely in diagnostics
    without reproducing the malformed character directly.
    """
    return f"{path}[{_quotePathKey(key)}]"


def _quotePathKey(value: str) -> str:
    """
    Returns one ASCII-safe quoted object-key representation for diagnostics.

    Printable ASCII characters are preserved except for quote and backslash,
    which are escaped. Control characters, non-ASCII characters, and surrogate
    code points are represented with hexadecimal Unicode escapes.
    """
    parts: list[str] = ['"']

    for character in value:
        codePoint = ord(character)

        if character == '"':
            parts.append('\\"')
        elif character == "\\":
            parts.append("\\\\")
        elif _PRINTABLE_ASCII_MIN <= codePoint <= _PRINTABLE_ASCII_MAX:
            parts.append(character)
        elif codePoint <= _BMP_CODE_POINT_MAX:
            parts.append(f"\\u{codePoint:04x}")
        else:
            parts.append(f"\\U{codePoint:08x}")

    parts.append('"')
    return "".join(parts)
