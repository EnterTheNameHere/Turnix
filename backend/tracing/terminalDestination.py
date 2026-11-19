# file: backend/tracing/terminalDestination.py ; version: 2
from __future__ import annotations

import datetime
import pprint
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TextIO

from backend.core.validation import requireBool, requireInstance, requireString
from backend.tracing.records import TraceRecord
from backend.tracing.typeDefinitions import TraceTypeDefinition

if TYPE_CHECKING:
    from backend.tracing.ids import TraceSpanId, TraceTypeDefinitionId

__all__: list[str] = [
    "TerminalColorMode",
    "TerminalTraceDestination",
]


type TerminalColorMode = Literal["auto", "always", "never"]

_RESET = "\x1b[0m"

_LEVEL_COLORS: dict[str, str] = {
    "debug": "\x1b[90m",
    "info": "\x1b[36m",
    "warning": "\x1b[33m",
    "error": "\x1b[31m",
    "fatal": "\x1b[1;31m",
}


@dataclass(frozen=True, slots=True)
class _SpanDisplayState:
    """
    Stores terminal-local display state for one observed active span.

    Depth reflects the indentation level assigned when the destination
    successfully rendered the span's primary start line.
    """

    depth: int


class TerminalTraceDestination:
    """
    Prints live best-effort trace information to a terminal stream.

    Trace-type definitions are cached for human-readable display metadata.
    Span nesting is reconstructed locally from successfully rendered primary
    span records and is therefore presentation state rather than authoritative
    trace state.

    Primary record lines establish display-state transitions. Supplementary
    attribute and exception rendering occurs only after those transitions are
    committed, so failures while rendering optional details do not leave
    already displayed span starts or ends in an inconsistent terminal state.

    The destination is thread-safe and serializes rendering through one
    reentrant lock.
    """

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        colorMode: TerminalColorMode = "auto",
        showAttributes: bool = True,
        showExceptions: bool = True,
    ) -> None:
        """
        Initializes a terminal trace destination.

        Args:
            stream:
                Text stream receiving rendered trace output. When omitted,
                output is written to ``sys.stdout``.
            colorMode:
                ANSI color policy. ``auto`` enables colors only when the
                stream reports itself as a terminal, ``always`` always emits
                ANSI color sequences, and ``never`` disables them.
            showAttributes:
                Whether nonempty record attributes are rendered below the
                primary record line.
            showExceptions:
                Whether attached exception snapshots are rendered below the
                primary record line.

        Raises:
            TypeError:
                If colorMode, showAttributes, or showExceptions violates its
                runtime type contract.
            ValueError:
                If colorMode is not ``auto``, ``always``, or ``never``.

        """
        cleanColorMode = requireString(colorMode, "colorMode")
        if cleanColorMode not in ("auto", "always", "never"):
            raise ValueError(
                "colorMode must be one of 'auto', 'always', or 'never'.",
            )

        self._stream = sys.stdout if stream is None else stream
        self._colorMode = cleanColorMode
        self._showAttributes = requireBool(showAttributes, "showAttributes")
        self._showExceptions = requireBool(showExceptions, "showExceptions")
        self._lock = threading.RLock()
        self._definitions: dict[
            TraceTypeDefinitionId,
            TraceTypeDefinition,
        ] = {}
        self._activeSpans: dict[TraceSpanId, _SpanDisplayState] = {}

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        """
        Caches one trace-type definition for later presentation.

        Repeated delivery of the same deterministic identity replaces the
        cached value with the supplied equivalent definition.

        Args:
            definition:
                Portable trace-type definition to cache.

        Raises:
            TypeError:
                If definition is not a TraceTypeDefinition.

        """
        cleanDefinition = requireInstance(
            definition,
            TraceTypeDefinition,
            "definition",
        )

        with self._lock:
            self._definitions[
                cleanDefinition.traceTypeDefinitionId
            ] = cleanDefinition

    def write(self, record: TraceRecord) -> None:
        """
        Renders one trace record using best-effort local span nesting.

        The primary line is written before terminal-local span display state is
        changed. Once the primary line succeeds, span-start or span-terminal
        state is committed immediately. Optional attribute and exception
        rendering then follows.

        Args:
            record:
                Immutable normalized trace record to render.

        Raises:
            TypeError:
                If record is not a TraceRecord.
            Exception:
                Any exception raised by the configured stream while rendering.
                TracePublisher normally isolates such destination failures from
                ordinary tracing.

        """
        cleanRecord = requireInstance(record, TraceRecord, "record")

        with self._lock:
            depth, unknownParent = self._resolveDepth(cleanRecord)

            self._writePrimaryLine(
                cleanRecord,
                depth=depth,
                unknownParent=unknownParent,
            )

            self._commitPrimaryRecordState(cleanRecord, depth=depth)

            if self._showAttributes and cleanRecord.attributes:
                self._writeIndentedValue(
                    "attributes",
                    cleanRecord.attributes,
                    depth=depth + 1,
                )

            if (
                self._showExceptions
                and cleanRecord.exceptionSnapshot is not None
            ):
                self._writeException(cleanRecord, depth=depth + 1)

    def _resolveDepth(self, record: TraceRecord) -> tuple[int, bool]:
        """
        Resolves presentation depth without mutating terminal span state.

        Args:
            record:
                Record whose display depth is being resolved.

        Returns:
            A pair containing the indentation depth and whether the record's
            required parent span is unknown to this destination.

        """
        if record.kind == "spanStart":
            if record.parentSpanId is None:
                return 0, False

            parentState = self._activeSpans.get(record.parentSpanId)
            if parentState is None:
                return 0, True

            return parentState.depth + 1, False

        if record.spanId is None:
            return 0, False

        spanState = self._activeSpans.get(record.spanId)
        if spanState is None:
            return 0, True

        if record.kind == "event":
            return spanState.depth + 1, False

        return spanState.depth, False

    def _commitPrimaryRecordState(
        self,
        record: TraceRecord,
        *,
        depth: int,
    ) -> None:
        """
        Commits terminal-local span state after primary-line rendering.

        Span starts become active only after their primary line has been
        written successfully. Span ends and verified abandoned-span evidence
        remove active display state immediately after their primary line is
        rendered, before optional detail rendering begins.

        Args:
            record:
                Successfully primary-rendered record.
            depth:
                Display depth used for the primary line.

        """
        if record.kind == "spanStart":
            if record.spanId is not None:
                self._activeSpans[
                    record.spanId
                ] = _SpanDisplayState(
                    depth=depth,
                )
            return

        if (
            record.spanId is not None
            and (
                record.kind == "spanEnd"
                or self._isSpanAbandoned(record)
            )
        ):
            self._activeSpans.pop(record.spanId, None)

    def _writePrimaryLine(
        self,
        record: TraceRecord,
        *,
        depth: int,
        unknownParent: bool,
    ) -> None:
        """
        Writes and flushes one record's primary terminal line.

        Args:
            record:
                Record to render.
            depth:
                Indentation depth assigned to the record.
            unknownParent:
                Whether terminal-local state lacks the required parent span.

        """
        timestamp = _formatTimestamp(record.timestampUnixNs)
        level = record.level.upper().ljust(7)
        coloredLevel = self._color(level, record.level)
        indentation = "  " * depth
        unknownText = " [unknown parent]" if unknownParent else ""
        domainText = f" {record.domain}" if record.domain else ""
        displayText = self._resolveDisplayText(record)
        displaySuffix = f" - {displayText}" if displayText else ""
        durationSuffix = _formatDuration(record.durationNs)
        messageSuffix = f": {record.message}" if record.message else ""

        line = (
            f"[{timestamp}] {coloredLevel}{domainText} "
            f"{indentation}{record.type}{displaySuffix}"
            f"{durationSuffix}{unknownText}{messageSuffix}\n"
        )

        self._stream.write(line)
        self._stream.flush()

    def _isSpanAbandoned(self, record: TraceRecord) -> bool:
        """
        Determines whether a record is verified abandoned-span evidence.

        Both the concrete record type and its registered portable definition
        must identify the special ``trace.span-abandoned`` event. This prevents
        a record-local label from spoofing terminal span cleanup.

        Args:
            record:
                Record to inspect.

        Returns:
            True when the record is verified abandoned-span evidence,
            otherwise False.

        """
        if record.type != "trace.span-abandoned":
            return False

        definition = self._definitions.get(record.traceTypeDefinitionId)

        return (
            definition is not None
            and definition.definitionKind == "event"
            and definition.name == "trace.span-abandoned"
        )

    def _resolveDisplayText(self, record: TraceRecord) -> str:
        """
        Resolves human-readable display text for one record.

        Args:
            record:
                Record whose registered generated-type metadata is inspected.

        Returns:
            The generated type's display name, or an empty string when the
            required definition or generated metadata is unavailable.

        """
        definition = self._definitions.get(record.traceTypeDefinitionId)
        if definition is None:
            return ""

        generated = definition.getGeneratedTypeForRecord(
            recordType=record.type,
            outcome=record.outcome,
        )
        if generated is None:
            return ""

        return generated.displayName

    def _writeIndentedValue(
        self,
        name: str,
        value: object,
        *,
        depth: int,
    ) -> None:
        """
        Pretty-prints one named value at the requested indentation depth.

        Args:
            name:
                Human-readable label preceding the rendered value.
            value:
                Value to render.
            depth:
                Indentation depth of the first rendered line.

        """
        indentation = "  " * depth
        rendered = pprint.pformat(
            _toDisplayValue(value),
            sort_dicts=True,
            width=100,
        )
        lines = rendered.splitlines() or [""]

        self._stream.write(f"{indentation}{name}: {lines[0]}\n")

        continuation = " " * (len(name) + 2)
        for line in lines[1:]:
            self._stream.write(f"{indentation}{continuation}{line}\n")

        self._stream.flush()

    def _writeException(
        self,
        record: TraceRecord,
        *,
        depth: int,
    ) -> None:
        """
        Renders an attached exception snapshot.

        Structured notes, exception attributes, catcher attributes, capture
        issues, and formatted stack text are rendered when present.

        Args:
            record:
                Record carrying the exception snapshot.
            depth:
                Indentation depth of the exception heading.

        """
        snapshot = record.exceptionSnapshot
        if snapshot is None:
            return

        indentation = "  " * depth

        self._stream.write(
            f"{indentation}exception: "
            f"{snapshot.typeModule}.{snapshot.typeQualifiedName}: "
            f"{snapshot.message}\n",
        )

        if snapshot.notes:
            self._writeIndentedValue(
                "notes",
                snapshot.notes,
                depth=depth + 1,
            )

        if snapshot.exceptionAttributes:
            self._writeIndentedValue(
                "exceptionAttributes",
                snapshot.exceptionAttributes,
                depth=depth + 1,
            )

        if snapshot.catcherAttributes:
            self._writeIndentedValue(
                "catcherAttributes",
                snapshot.catcherAttributes,
                depth=depth + 1,
            )

        if snapshot.captureIssues:
            self._writeIndentedValue(
                "captureIssues",
                snapshot.captureIssues,
                depth=depth + 1,
            )

        if snapshot.stack:
            for line in snapshot.stack.rstrip().splitlines():
                self._stream.write(f"{indentation}  {line}\n")

        self._stream.flush()

    def _color(self, text: str, level: str) -> str:
        """
        Applies ANSI color for one trace level when enabled.

        Args:
            text:
                Text to colorize.
            level:
                Validated trace level selecting the ANSI color sequence.

        Returns:
            Colorized text when color output is enabled, otherwise the original
            text unchanged.

        """
        if not self._useColor():
            return text

        return f"{_LEVEL_COLORS[level]}{text}{_RESET}"

    def _useColor(self) -> bool:
        """
        Determines whether ANSI color sequences should be emitted.

        Returns:
            True for ``always`` mode, False for ``never`` mode, or the
            best-effort result of the stream's callable ``isatty`` operation
            in ``auto`` mode.

        """
        if self._colorMode == "always":
            return True

        if self._colorMode == "never":
            return False

        isatty = getattr(self._stream, "isatty", None)
        return bool(callable(isatty) and isatty())


def _formatTimestamp(timestampUnixNs: int) -> str:
    """
    Formats a Unix timestamp for terminal presentation.

    Millisecond precision is used for ordinary human-readable timestamps. If
    the supplied nonnegative Unix timestamp cannot be represented by the local
    datetime implementation, the exact nanosecond value is preserved in an
    unambiguous fallback representation.

    Args:
        timestampUnixNs:
            Unix timestamp in nanoseconds.

    Returns:
        Local human-readable clock text or an exact ``unix-ns:<value>``
        fallback.

    """
    seconds, nanoseconds = divmod(timestampUnixNs, 1_000_000_000)
    milliseconds = nanoseconds // 1_000_000

    try:
        localTime = datetime.datetime.fromtimestamp(
            seconds,
            tz=datetime.UTC,
        ).astimezone()
    except (OverflowError, OSError, ValueError):
        return f"unix-ns:{timestampUnixNs}"

    return f"{localTime:%H:%M:%S}.{milliseconds:03d}"


def _formatDuration(durationNs: int | None) -> str:
    """
    Formats an optional duration for compact terminal presentation.

    Args:
        durationNs:
            Duration in nanoseconds, or None when no duration is available.

    Returns:
        An empty string when duration is absent, otherwise a bracketed value
        expressed in microseconds, milliseconds, or seconds.

    """
    if durationNs is None:
        return ""

    milliseconds = durationNs / 1_000_000

    if milliseconds < 1:
        return f" [{durationNs / 1_000:.3f} µs]"

    if milliseconds < 1_000:  # noqa: PLR2004
        return f" [{milliseconds:.3f} ms]"

    return f" [{milliseconds / 1_000:.3f} s]"


def _toDisplayValue(value: object) -> object:
    """
    Converts immutable container values into pprint-friendly structures.

    Mapping implementations are copied into ordinary dictionaries while tuple
    structure is preserved recursively. Scalar values are returned unchanged.

    Args:
        value:
            Value to prepare for terminal rendering.

    Returns:
        A recursively presentation-friendly value.

    """
    if isinstance(value, Mapping):
        return {
            key: _toDisplayValue(item)
            for key, item in value.items()
        }

    if type(value) is tuple:
        return tuple(_toDisplayValue(item) for item in value)

    return value
