# file: backend/tracing/destinations.py ; version: 3
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from backend.tracing.records import TraceRecord
    from backend.tracing.typeDefinitions import TraceTypeDefinition

__all__: list[str] = [
    "TraceDestination",
    "TraceSinkDestination",
]


class TraceDestination(Protocol):
    """
    Receives trace definitions and immutable records from one or more Tracers.

    A destination object may be registered with multiple Tracers. Each Tracer
    owns an independent Publisher and registration state, so calls from
    different Tracers may occur concurrently. A destination instance that is
    shared between Tracers must therefore serialize or otherwise make its own
    mutable state thread-safe.

    Equivalent trace-type definitions may be delivered more than once when a
    destination is shared by independent Tracers or is removed and re-added.
    Implementations must treat repeated equivalent definition delivery as
    idempotent.
    """

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        """Receives one trace-type definition."""
        ...

    def write(self, record: TraceRecord) -> None:
        """Receives one immutable trace record."""
        ...


class TraceSinkDestination:
    """Discards all received tracing evidence."""

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        """Discards one trace-type definition."""
        return

    def write(self, record: TraceRecord) -> None:
        """Discards one trace record."""
        return
