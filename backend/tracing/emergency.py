# file: backend/tracing/emergency.py ; version: 3
from __future__ import annotations

import sys
import threading
from typing import TYPE_CHECKING, TextIO

from backend.core.validation import requireString, typeName

if TYPE_CHECKING:
    from backend.tracing.destinations import TraceDestination
    from backend.tracing.ids import TraceDestinationRegistrationId, TraceEventId, TraceSpanId

__all__: list[str] = [
    "TraceEmergencyReporter",
]


class TraceEmergencyReporter:
    """
    Reports tracing-infrastructure diagnostics without using tracing itself.

    Emergency reporting is deliberately isolated from the ordinary tracing
    pipeline so destination-health transitions and publication, context, and
    finalization failures can be surfaced without risking recursive trace
    emission.

    Destination-health episode tracking is owned by the Publisher. This
    reporter does not deduplicate failures or determine recovery; callers
    report only the health transitions that should be surfaced.

    Writes are serialized across threads. Ordinary exceptions raised by the
    configured stream are suppressed so emergency reporting does not replace
    or mask the tracing failure being reported.
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
        registrationId: TraceDestinationRegistrationId,
        destination: TraceDestination,
        err: Exception,
    ) -> None:
        """
        Reports entry into a failed destination-delivery health episode.

        The diagnostic identifies the registration, operation that began the
        failure episode, destination runtime type, exception runtime type, and
        best-effort exception message.

        Health-state tracking and repeated-failure suppression are owned by the
        caller; this method writes one supplied diagnostic without maintaining
        destination state.

        Args:
            operation:
                Name of the destination operation that began the failure
                episode.
            registrationId:
                Registration identifier whose delivery health became failed.
            destination:
                Destination associated with the failed registration.
            err:
                Exception that began the failure episode.

        """
        self._write(
            "[ACTANT TRACE DESTINATION FAILURE] "
            f"registration={registrationId} {operation} failed for "
            f"{typeName(destination)}: "
            f"{typeName(err)}: {self._safeString(err)}",
        )

    def reportDestinationRecovered(
        self,
        *,
        registrationId: TraceDestinationRegistrationId,
        destination: TraceDestination,
    ) -> None:
        """
        Reports recovery of one failed destination-delivery health episode.

        Recovery determination is owned by the caller; this method writes one
        supplied recovery diagnostic without maintaining destination state.

        Args:
            registrationId:
                Registration identifier whose delivery health recovered.
            destination:
                Destination associated with the recovered registration.

        """
        self._write(
            "[ACTANT TRACE DESTINATION RECOVERED] "
            f"registration={registrationId} "
            f"destination={typeName(destination)}.",
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
                Identifier of the span whose abandonment evidence could not be
                published normally.
            err:
                Exception raised while publishing abandonment evidence.

        """
        self._write(
            "[ACTANT TRACE ABANDONMENT FAILURE] Could not publish abandonment "
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
