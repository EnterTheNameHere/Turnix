# file: backend/tracing/destinations.py ; version: 2
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
    """Receives trace-type definitions and immutable trace records."""

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        """Receives one current-lifecycle trace-type definition."""
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
