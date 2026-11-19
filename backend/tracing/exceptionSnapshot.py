# file: backend/tracing/exceptionSnapshot.py ; version: 2
from __future__ import annotations

import contextlib
import traceback
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from backend.core.collections import immutableMapping
from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer
from backend.core.validation import requireBool, requireInstance, requireString, typeName

__all__: list[str] = [
    "ExceptionSnapshot",
    "captureExceptionSnapshot",
]


_EMPTY_VALUES: Mapping[str, ImmutableValue] = immutableMapping({})
_EXCEPTION_NOTES_ATTRIBUTE = "__notes__"


@dataclass(frozen=True, slots=True)
class ExceptionSnapshot:
    """
    Represents an immutable saveable snapshot of one observed exception.

    The snapshot contains portable descriptive information about the
    exception without retaining the live exception object. Exception and
    catcher attributes are recursively frozen into immutable values.

    captureIssues records failures encountered while best-effort exception
    metadata was being inspected or converted.
    """

    typeName: str
    typeQualifiedName: str
    typeModule: str
    message: str
    representation: str
    stack: str | None
    notes: tuple[str, ...] = ()
    exceptionAttributes: Mapping[str, ImmutableValue] = field(
        default_factory=lambda: _EMPTY_VALUES,
    )
    catcherAttributes: Mapping[str, ImmutableValue] = field(
        default_factory=lambda: _EMPTY_VALUES,
    )
    captureIssues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """
        Validates and normalizes immutable snapshot content.

        Sequence fields are copied into tuples. Exception and catcher
        attributes are recursively copied into immutable mappings so the
        snapshot does not retain caller-owned mutable containers.

        Raises:
            TypeError:
                If a scalar field, sequence member, mapping key, or immutable
                value has an unsupported type.
            ValueError:
                If an attribute value violates immutable-value constraints.

        """
        requireString(self.typeName, "typeName")
        requireString(self.typeQualifiedName, "typeQualifiedName")
        requireString(self.typeModule, "typeModule")
        requireString(self.message, "message")
        requireString(self.representation, "representation")

        if self.stack is not None:
            requireString(self.stack, "stack")

        notes = tuple(self.notes)
        for index, note in enumerate(notes):
            requireString(note, f"notes[{index}]")
        object.__setattr__(self, "notes", notes)

        issues = tuple(self.captureIssues)
        for index, issue in enumerate(issues):
            requireString(issue, f"captureIssues[{index}]")
        object.__setattr__(self, "captureIssues", issues)

        freezer = ImmutableValueFreezer()
        object.__setattr__(
            self,
            "exceptionAttributes",
            freezer.freezeMapping(
                cast(Mapping[object, object], self.exceptionAttributes),
                "exceptionAttributes",
            ),
        )
        object.__setattr__(
            self,
            "catcherAttributes",
            freezer.freezeMapping(
                cast(Mapping[object, object], self.catcherAttributes),
                "catcherAttributes",
            ),
        )


def captureExceptionSnapshot(
    err: BaseException,
    *,
    catcherAttributes: Mapping[object, object] | None = None,
    includeStack: bool = True,
) -> ExceptionSnapshot:
    """
    Captures immutable evidence about an observed exception.

    Exception type information, message text, representation, notes,
    attributes, and optionally the formatted traceback are copied without
    retaining the live exception object.

    Inspection of exception-owned metadata is best-effort. Failures while
    reading or converting such metadata are represented by fallback values
    and recorded in captureIssues where possible. Unsupported exception
    attribute values fall back to their representation.

    catcherAttributes represents metadata supplied by the code observing the
    exception rather than metadata owned by the exception itself. It must
    satisfy the normal immutable-value contract and is not silently degraded
    when invalid.

    Args:
        err:
            Exception to snapshot.
        catcherAttributes:
            Optional immutable-value metadata supplied by the catcher.
        includeStack:
            Whether to capture the exception's formatted traceback.

    Returns:
        A detached immutable snapshot of the observed exception.

    Raises:
        TypeError:
            If err is not an exception, includeStack is not an exact boolean,
            or catcherAttributes contains unsupported immutable-value data.
        ValueError:
            If catcherAttributes violates immutable-value constraints.

    """
    requireInstance(err, BaseException, "err")
    requireBool(includeStack, "includeStack")

    issues: list[str] = []
    exceptionType = type(err)

    typeNameValue = _safeClassString(
        exceptionType,
        "__name__",
        "<unknown type>",
        issues,
    )
    typeQualifiedName = _safeClassString(
        exceptionType,
        "__qualname__",
        "<unknown qualified type>",
        issues,
    )
    typeModule = _safeClassString(
        exceptionType,
        "__module__",
        "<unknown module>",
        issues,
    )
    message = _safeString(
        err,
        "<exception message unavailable>",
        issues,
    )
    representation = _safeRepr(
        err,
        "<exception representation unavailable>",
        issues,
    )

    stack: str | None = None
    if includeStack:
        try:
            stack = "".join(
                traceback.format_exception(
                    exceptionType,
                    err,
                    err.__traceback__,
                ),
            )
        except Exception as formattingError:  # noqa: BLE001
            issues.append(
                "stack formatting failed with "
                f"{typeName(formattingError)}",
            )

    notes = _captureNotes(err, issues)
    exceptionAttributes = _captureExceptionAttributes(err, issues)
    frozenCatcherAttributes = ImmutableValueFreezer().freezeMapping(
        catcherAttributes,
        "catcherAttributes",
    )

    return ExceptionSnapshot(
        typeName=typeNameValue,
        typeQualifiedName=typeQualifiedName,
        typeModule=typeModule,
        message=message,
        representation=representation,
        stack=stack,
        notes=notes,
        exceptionAttributes=exceptionAttributes,
        catcherAttributes=frozenCatcherAttributes,
        captureIssues=tuple(issues),
    )


def _safeClassString(
    exceptionType: type[BaseException],
    attributeName: str,
    fallback: str,
    issues: list[str],
) -> str:
    """
    Returns one exact string attribute from an exception type.

    Access or type failures are converted to fallback and appended to issues.
    """
    try:
        value = getattr(exceptionType, attributeName)
    except Exception as err:  # noqa: BLE001
        issues.append(
            f"exception type {attributeName} access "
            f"failed with {typeName(err)}",
        )
        return fallback

    if type(value) is str:
        return value

    issues.append(f"exception type {attributeName} was not a string")
    return fallback


def _safeString(
    value: object,
    fallback: str,
    issues: list[str],
) -> str:
    """
    Returns an exact string conversion or a recorded fallback.

    Ordinary conversion failures are appended to issues and do not abort
    exception snapshot capture.
    """
    try:
        result = str(value)
    except Exception as err:  # noqa: BLE001
        issues.append(f"string conversion failed with {typeName(err)}")
        return fallback

    if type(result) is not str:
        issues.append("string conversion did not return an exact string")
        return fallback

    return result


def _safeRepr(
    value: object,
    fallback: str,
    issues: list[str],
) -> str:
    """
    Returns an exact representation string or a recorded fallback.

    Ordinary representation failures are appended to issues and do not abort
    exception snapshot capture.
    """
    try:
        result = repr(value)
    except Exception as err:  # noqa: BLE001
        issues.append(f"repr conversion failed with {typeName(err)}")
        return fallback

    if type(result) is not str:
        issues.append("repr conversion did not return an exact string")
        return fallback

    return result


def _captureNotes(
    err: BaseException,
    issues: list[str],
) -> tuple[str, ...]:
    """
    Captures exception notes as detached strings.

    Non-string note values are converted best-effort. Inspection failures are
    recorded and produce an empty notes tuple.
    """
    with contextlib.suppress(Exception):
        rawNotes = getattr(err, _EXCEPTION_NOTES_ATTRIBUTE, None)

        if rawNotes is None:
            return ()

        notes: list[str] = []
        for index, rawNote in enumerate(rawNotes):
            if type(rawNote) is str:
                notes.append(rawNote)
            else:
                notes.append(
                    _safeString(
                        rawNote,
                        f"<note {index} unavailable>",
                        issues,
                    ),
                )

        return tuple(notes)

    issues.append("exception notes could not be inspected")
    return ()


def _captureExceptionAttributes(
    err: BaseException,
    issues: list[str],
) -> Mapping[str, ImmutableValue]:
    """
    Captures custom exception attributes as detached immutable values.

    The standard __notes__ attribute is omitted because notes are normalized
    separately. Unsupported attribute values fall back to their representation
    so one problematic attribute does not discard the remaining metadata.
    """
    try:
        rawAttributes = getattr(err, "__dict__", None)
    except Exception as accessError:  # noqa: BLE001
        issues.append(
            "exception __dict__ access failed with "
            f"{typeName(accessError)}",
        )
        return _EMPTY_VALUES

    if rawAttributes is None:
        return _EMPTY_VALUES

    if not isinstance(rawAttributes, Mapping):
        issues.append("exception __dict__ was not a mapping")
        return _EMPTY_VALUES

    freezer = ImmutableValueFreezer()
    captured: dict[str, ImmutableValue] = {}

    for rawName, rawValue in rawAttributes.items():
        if type(rawName) is not str:
            issues.append(
                "exception attribute with non-string name was omitted",
            )
            continue

        if rawName == _EXCEPTION_NOTES_ATTRIBUTE:
            continue

        try:
            captured[rawName] = freezer.freeze(
                rawValue,
                f"exceptionAttributes[{rawName!r}]",
            )
        except Exception as freezeError:  # noqa: BLE001
            captured[rawName] = _safeRepr(
                rawValue,
                "<attribute unavailable>",
                issues,
            )
            issues.append(
                f"exception attribute {rawName!r} required repr "
                f"fallback after {typeName(freezeError)}",
            )

    return immutableMapping(captured)
