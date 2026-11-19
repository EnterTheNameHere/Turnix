# file: backend/tracing/validation.py ; version: 2
from __future__ import annotations

import re
from typing import Literal, cast

from backend.core.validation import requireExactNonBlankString, requireString

__all__: list[str] = [
    "TRACE_LEVELS",
    "TRACE_RECORD_KINDS",
    "TraceLevel",
    "TraceRecordKind",
    "requireDisplayName",
    "requireName",
    "requireOrigin",
    "requireOutcomeName",
    "requireTraceLevel",
    "requireTraceRecordKind",
]


type TraceLevel = Literal[
    "debug",
    "info",
    "warning",
    "error",
    "fatal",
]

type TraceRecordKind = Literal[
    "event",
    "spanStart",
    "spanEnd",
]


TRACE_LEVELS: tuple[str, ...] = (
    "debug",
    "info",
    "warning",
    "error",
    "fatal",
)

TRACE_RECORD_KINDS: tuple[str, ...] = (
    "event",
    "spanStart",
    "spanEnd",
)

_NAME_PATTERN = re.compile(r"[a-z0-9$#@&!+_-]+(?:\.[a-z0-9$#@&!+_-]+)*")
_OUTCOME_PATTERN = re.compile(r"[a-z0-9$#@&!+_-]+")


def requireName(value: str, name: str) -> str:
    """
    Validates one lowercase dotted tracing name.

    A tracing name consists of one or more nonempty segments separated by
    periods. Each segment may contain lowercase ASCII letters, digits, '$',
    '#', '@', '&', '!', '+', '-', or '_'. Leading, trailing, repeated, or
    otherwise empty segments are rejected.

    Args:
        value:
            Tracing name to validate.
        name:
            Diagnostic name identifying value in validation errors.

    Returns:
        The exact validated string supplied by the caller.

    Raises:
        TypeError:
            If value or name is not an exact built-in string.
        ValueError:
            If value is blank, contains surrounding whitespace, does not use
            the tracing-name syntax, or name is blank or contains surrounding
            whitespace.

    """
    cleanValue = requireExactNonBlankString(value, name)
    if _NAME_PATTERN.fullmatch(cleanValue) is None:
        raise ValueError(
            f"{name} must be a lowercase dotted name using only digits, "
            "lowercase ASCII letters, '$', '#', '@', '&', '!', '+', '-', "
            f"or '_'; received '{cleanValue}'.",
        )
    return cleanValue


def requireOutcomeName(value: str, name: str = "outcome") -> str:
    """
    Validates one lowercase undotted tracing outcome or label.

    An outcome name consists of exactly one nonempty segment containing
    lowercase ASCII letters, digits, '$', '#', '@', '&', '!', '+', '-', or
    '_'. Periods and therefore dotted names are rejected.

    Args:
        value:
            Outcome or generated-label name to validate.
        name:
            Diagnostic name identifying value in validation errors.

    Returns:
        The exact validated string supplied by the caller.

    Raises:
        TypeError:
            If value or name is not an exact built-in string.
        ValueError:
            If value is blank, contains surrounding whitespace, does not use
            the tracing-outcome syntax, or name is blank or contains
            surrounding whitespace.

    """
    cleanValue = requireExactNonBlankString(value, name)
    if _OUTCOME_PATTERN.fullmatch(cleanValue) is None:
        raise ValueError(
            f"{name} must be one lowercase name segment using only digits, "
            "lowercase ASCII letters, '$', '#', '@', '&', '!', '+', '-', "
            f"or '_'; received '{cleanValue}'.",
        )
    return cleanValue


def requireOrigin(value: str, name: str = "origin") -> str:
    """
    Validates one trace-origin name.

    Trace origins use the same lowercase dotted syntax as tracing names.

    Args:
        value:
            Trace-origin name to validate.
        name:
            Diagnostic name identifying value in validation errors.

    Returns:
        The exact validated string supplied by the caller.

    Raises:
        TypeError:
            If value or name is not an exact built-in string.
        ValueError:
            If value is blank, contains surrounding whitespace, does not use
            the tracing-name syntax, or name is blank or contains surrounding
            whitespace.

    """
    return requireName(value, name)


def requireDisplayName(value: str, name: str = "displayName") -> str:
    """
    Validates human-readable trace display text.

    Display text may be empty and is otherwise preserved exactly. No
    normalization or tracing-name syntax restriction is applied.

    Args:
        value:
            Human-readable display text to validate.
        name:
            Diagnostic name identifying value in validation errors.

    Returns:
        The exact validated string supplied by the caller.

    Raises:
        TypeError:
            If value or name is not an exact built-in string.
        ValueError:
            If name is blank or contains surrounding whitespace.

    """
    return requireString(value, name)


def requireTraceLevel(value: str, name: str = "level") -> TraceLevel:
    """
    Validates one canonical trace presentation level.

    Accepted levels are the values listed in TRACE_LEVELS.

    Args:
        value:
            Trace presentation level to validate.
        name:
            Diagnostic name identifying value in validation errors.

    Returns:
        The validated canonical trace level.

    Raises:
        TypeError:
            If value or name is not an exact built-in string.
        ValueError:
            If value is blank, contains surrounding whitespace, is not a
            canonical trace level, or name is blank or contains surrounding
            whitespace.

    """
    cleanValue = requireExactNonBlankString(value, name)
    if cleanValue not in TRACE_LEVELS:
        allowed = ", ".join(repr(level) for level in TRACE_LEVELS)
        raise ValueError(
            f"{name} must be one of {allowed}; "
            f"received '{cleanValue}'.",
        )
    return cast(TraceLevel, cleanValue)


def requireTraceRecordKind(value: str, name: str = "kind") -> TraceRecordKind:
    """
    Validates one canonical trace-record kind.

    Accepted record kinds are the values listed in TRACE_RECORD_KINDS.

    Args:
        value:
            Trace-record kind to validate.
        name:
            Diagnostic name identifying value in validation errors.

    Returns:
        The validated canonical trace-record kind.

    Raises:
        TypeError:
            If value or name is not an exact built-in string.
        ValueError:
            If value is blank, contains surrounding whitespace, is not a
            canonical trace-record kind, or name is blank or contains
            surrounding whitespace.

    """
    cleanValue = requireExactNonBlankString(value, name)
    if cleanValue not in TRACE_RECORD_KINDS:
        allowed = ", ".join(repr(kind) for kind in TRACE_RECORD_KINDS)
        raise ValueError(
            f"{name} must be one of {allowed}; "
            f"received '{cleanValue}'.",
        )
    return cast(TraceRecordKind, cleanValue)
