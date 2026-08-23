# file: backend/values/validation.py ; version: 2
from __future__ import annotations

from backend.core.validation import requireExactNonBlankString, typeName

__all__: list[str] = [
    "requireRelativeValueAddress",
    "requireValueAddress",
    "requireValueAddressSegment",
]

_ALLOWED_FIRST_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789",
)
_ALLOWED_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789._-",
)


def requireValueAddressSegment(value: str, name: str) -> str:
    """
    Validates one canonical Value System address segment.

    A ValueAddress segment is machine-facing identity rather than
    human-facing text. Its syntax is deliberately restrictive so equivalent
    or visually confusing spellings do not become distinct identities across
    programming languages, storage systems, transports, or tools.

    A valid segment:
        - is an exact built-in string;
        - is not blank;
        - contains ASCII characters only;
        - starts with a lowercase ASCII letter or digit;
        - contains only lowercase ASCII letters, digits, ".", "_", and "-"
          after its first character.

    Validation never normalizes, trims, case-folds, escapes, unescapes, or
    otherwise changes the supplied segment. A successful result is therefore
    textually identical to the supplied value.

    Unicode text, human-readable names, aliases, labels, descriptions, and
    other expressive metadata belong to values or metadata associated with an
    address rather than to canonical ValueAddress identity.

    Args:
        value:
            Segment text to validate.
        name:
            Diagnostic name used when reporting invalid input.

    Returns:
        The original validated string unchanged.

    Raises:
        TypeError:
            If value or name is not an exact built-in string.
        ValueError:
            If value is blank, is not ASCII, begins with an invalid
            character, contains an invalid character, or name is blank
            or contains surrounding whitespace.

    """
    cleanName = requireExactNonBlankString(name, "name")

    if type(value) is not str:
        raise TypeError(
            f"{cleanName} must be an exact built-in string; "
            f"received {typeName(value)}.",
        )

    if not value:
        raise ValueError(
            f"{cleanName} must not be empty.\n"
            f"received: {value!r}",
        )

    if not value.isascii():
        invalidCharacter = next(
            character for character in value if not character.isascii()
        )
        raise ValueError(
            f"{cleanName} must contain ASCII characters only.\n"
            f"received: {value!r}\n"
            f"invalid character: {invalidCharacter!r}",
        )

    firstCharacter = value[0]

    if firstCharacter not in _ALLOWED_FIRST_CHARACTERS:
        raise ValueError(
            f"{cleanName} must start with a lowercase ASCII letter or digit.\n"
            f"received: {value!r}\n"
            f"invalid first character: {firstCharacter!r}",
        )

    for character in value[1:]:
        if character not in _ALLOWED_CHARACTERS:
            raise ValueError(
                f"{cleanName} contains invalid character {character!r}.\n"
                f"received: {value!r}\n"
                "ValueAddress segments allow only lowercase ASCII letters, "
                'digits, ".", "_", and "-".',
            )

    return value


def requireValueAddress(value: str, name: str) -> str:
    """
    Validates one canonical address in the Actant Value System.

    A ValueAddress identifies one logical value independently of the
    representation, storage provider, programming language, or runtime object
    used to realize that value.

    Canonical ValueAddress syntax is deliberately restrictive. An address:
        - is an exact built-in string;
        - contains ASCII characters only;
        - contains lowercase characters only;
        - consists of one or more non-empty segments;
        - uses "/" as the only segment separator;
        - uses no escape syntax;
        - is already canonical when supplied.

    Each segment starts with a lowercase ASCII letter or digit. Remaining
    segment characters may be lowercase ASCII letters, digits, ".", "_", or
    "-".

    Validation never normalizes, trims, lowercases, escapes, unescapes,
    collapses separators, resolves relative components, or otherwise repairs
    the supplied value. If validation succeeds, the returned text is exactly
    the text supplied by the caller.

    Address identity is separate from future query or selector syntax.
    Wildcards, predicates, ranges, pattern operators, and similar selection
    syntax are therefore not accepted as ValueAddress characters merely
    because a future Value System query language may use them.

    Unicode human-readable names, aliases, descriptions, labels, searchable
    text, and other expressive content belong to values or metadata associated
    with an address rather than to the canonical address itself.

    Args:
        value:
            Complete address text to validate.
        name:
            Diagnostic name used when reporting invalid input.

    Returns:
        The original validated string unchanged.

    Raises:
        TypeError:
            If value or name is not an exact built-in string.
        ValueError:
            If the address is empty, is not ASCII, starts or ends with "/",
            contains an empty segment, contains an invalid segment, or name
            is blank or contains surrounding whitespace.

    """
    cleanName = requireExactNonBlankString(name, "name")

    if type(value) is not str:
        raise TypeError(
            f"{cleanName} must be an exact built-in string; "
            f"received {typeName(value)}.",
        )

    if not value:
        raise ValueError(
            f"{cleanName} must not be empty.\n"
            f"received: {value!r}",
        )

    if not value.isascii():
        invalidCharacter = next(
            character for character in value if not character.isascii()
        )
        raise ValueError(
            f"{cleanName} must contain ASCII characters only.\n"
            f"received: {value!r}\n"
            f"invalid character: {invalidCharacter!r}",
        )

    if value.startswith("/"):
        raise ValueError(
            f"{cleanName} must not start with '/'.\n"
            f"received: {value!r}",
        )

    if value.endswith("/"):
        raise ValueError(
            f"{cleanName} must not end with '/'.\n"
            f"received: {value!r}",
        )

    segments = value.split("/")

    for index, segment in enumerate(segments, start=1):
        if not segment:
            raise ValueError(
                f"{cleanName} contains an empty segment at position {index}.\n"
                f"received: {value!r}\n"
                "If '//' was intentional, it is not valid in a ValueAddress; "
                "use a single '/' between segments.",
            )

        try:
            requireValueAddressSegment(segment, f"{cleanName} segment {index}")
        except ValueError as err:
            raise _wrapSegmentDiagnostic(
                err,
                address=value,
                segment=segment,
            ) from err

    return value


def _wrapSegmentDiagnostic(
    error: ValueError,
    *,
    address: str,
    segment: str,
) -> ValueError:
    """
    Reframes a segment validation failure as a complete-address diagnostic.

    The segment validator remains authoritative for the failure reason and
    detailed diagnosis. Its segment-local ``received: `` line is replaced by
    the complete address, while the offending segment is included separately.

    This preserves useful details such as the exact invalid character without
    losing the address context in which the segment appeared.
    """
    lines = str(error).splitlines()

    detailLines = (
        lines[2:]
        if len(lines) >= 2  # noqa: PLR2004
        and lines[1].startswith("received: ")
        else lines[1:]
    )

    wrappedLines = [
        lines[0],
        f"received: {address!r}",
        f"segment: {segment!r}",
        *detailLines,
    ]

    return ValueError("\n".join(wrappedLines))


def requireRelativeValueAddress(value: str, name: str) -> str:
    """
    Validates one canonical relative address in the Actant Value System.

    RelativeValueAddress uses the same canonical textual grammar as
    ValueAddress, but its semantic meaning is different. A relative address
    does not identify a complete Value System identity by itself. It requires
    a base ValueAddress before it can be resolved to one.

    Relative addresses:
        - contain lowercase ASCII characters only;
        - consist of one or more non-empty segments;
        - use "/" as the only segment separator;
        - use no escape syntax;
        - are validated as already canonical and are never normalized.

    Relative addressing does not provide filesystem-style traversal. "." and
    ".." are not special traversal components and are invalid under the current
    ValueAddress segment grammar because a segment cannot begin with ".". A
    period may otherwise appear inside a segment according to ordinary
    ValueAddress segment grammar, but bears no special meaning.

    This validator deliberately delegates canonical syntax validation to
    requireValueAddress(). Absolute and relative Value System addresses use
    the same textual grammar; their distinction is semantic and represented
    by their respective value-object types.

    Args:
        value:
            Relative address text to validate.
        name:
            Diagnostic name used when reporting invalid input.

    Returns:
        The original validated string unchanged.

    Raises:
        TypeError:
            If value or name is not an exact built-in string.
        ValueError:
            If value is not canonical ValueAddress syntax or name is blank or
            contains surrounding whitespace.

    """
    return requireValueAddress(value, name)
