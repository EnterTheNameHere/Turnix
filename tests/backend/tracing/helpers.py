# file: tests/backend/tracing/helpers.py ; version: 2
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.tracing import TraceRecord, TraceTypeDefinition


@dataclass(slots=True)
class CollectingDestination:
    definitions: list[TraceTypeDefinition] = field(default_factory=list)
    records: list[TraceRecord] = field(default_factory=list)
    operations: list[tuple[str, object]] = field(default_factory=list)

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        self.definitions.append(definition)
        self.operations.append(("definition", definition))

    def write(self, record: TraceRecord) -> None:
        self.records.append(record)
        self.operations.append(("record", record))
