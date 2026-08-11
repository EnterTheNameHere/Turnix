# file: backend/tracing/emergency.py ; version: 2
from __future__ import annotations

import sys
import threading
from typing import TYPE_CHECKING, TextIO

from backend.core.validation import requireString, typeName

if TYPE_CHECKING:
    from backend.tracing.destinations import TraceDestination
    from backend.tracing.ids import TraceEventId, TraceSpanId

__all__: list[str] = [
    "TraceEmergencyReporter",
]


class TraceEmergencyReporter:
    """
    Reports tracing-infrastructure failures without using tracing itself.

    Emergency reporting is deliberately isolated from the ordinary tracing
    pipeline so destination, publication, context, and finalization failures
    can be surfaced without risking recursive trace emission.

    Writes are serialized across threads. Failures raised by the configured
    stream are suppressed so emergency reporting does not replace or mask the
    tracing failure being reported.
    """

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
    ) -> None:
        """
        Initializes an emergency reporter.

        Args:
            stream:
                Text stream that receives emergency diagnostics. When omitted,
                diagnostics are written to ``sys.stderr``.

        """
        self._stream = sys.stderr if stream is None else stream
        self._lock = threading.Lock()

    def reportDestinationFailure(
        self,
        *,
        operation: str,
        destination: TraceDestination,
        err: Exception,
    ) -> None:
        """
        Reports failure of one trace-destination operation.

        The diagnostic identifies the failed operation, destination runtime
        type, exception runtime type, and best-effort exception message.

        Args:
            operation:
                Human-readable name of the failed destination operation.
            destination:
                Destination whose operation failed.
            err:
                Exception raised by the destination.

        """
        self._write(
            "[ACTANT TRACE DESTINATION FAILURE] "
            f"{operation} failed for {typeName(destination)}: "
            f"{typeName(err)}: {self._safeString(err)}",
        )

    def reportRecursivePublication(
        self,
        *,
        eventId: TraceEventId | None,
    ) -> None:
        """
        Reports an ordinary trace-publication attempt made during publication.

        Args:
            eventId:
                Identifier of the record involved in recursion detection when
                known, otherwise ``None``.

        """
        eventText = "unknown" if eventId is None else str(eventId)
        self._write(
            "[ACTANT TRACE RECURSION] A destination attempted ordinary "
            "trace publication while consuming trace evidence; "
            f"event={eventText}.",
        )

    def reportAbandonmentFailure(
        self,
        *,
        spanId: TraceSpanId,
        err: Exception,
    ) -> None:
        """
        Reports failure to publish evidence for an abandoned active span.

        Args:
            spanId:
                Identifier of the span whose abandonment could not be
                reported normally.
            err:
                Exception raised while publishing abandonment evidence.

        """
        self._write(
            "[ACTANT TRACE ABANDONMENT FAILURE] Could not publish misuse "
            f"evidence for span {spanId}: {typeName(err)}: "
            f"{self._safeString(err)}",
        )

    def reportContextFailure(self, message: str) -> None:
        """
        Reports a tracing context or managed-finalization failure.

        Args:
            message:
                Exact diagnostic text describing the failure.

        Raises:
            TypeError:
                If message is not an exact built-in string.

        """
        self._write(
            "[ACTANT TRACE CONTEXT FAILURE] "
            + requireString(message, "message"),
        )

    def _safeString(self, value: object) -> str:
        """
        Returns best-effort string text without propagating conversion errors.

        A fixed fallback is returned when ``str(value)`` raises.
        """
        try:
            return str(value)
        except Exception:  # noqa: BLE001
            return "<message unavailable>"

    def _write(self, message: str) -> None:
        """
        Writes and flushes one emergency diagnostic without propagating errors.

        Writes are serialized by the reporter lock. Any ordinary exception
        raised by the configured stream is suppressed because emergency
        reporting must not introduce another tracing failure path.
        """
        try:
            with self._lock:
                self._stream.write(message + "\n")
                self._stream.flush()
        except Exception:  # noqa: BLE001
            return
