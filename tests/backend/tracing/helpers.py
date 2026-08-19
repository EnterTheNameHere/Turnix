# file: tests/backend/tracing/helpers.py ; version: 3
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.tracing.destinations import TraceSinkDestination
from backend.tracing.tracer import Tracer

if TYPE_CHECKING:
    from backend.tracing.records import TraceRecord
    from backend.tracing.typeDefinitions import TraceTypeDefinition


@dataclass(slots=True)
class CollectingDestination:
    definitions: list[TraceTypeDefinition] = field(default_factory=list)
    records: list[TraceRecord] = field(default_factory=list)
    operations: list[tuple[str, object]] = field(default_factory=list)
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )

    def writeTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        with self._lock:
            self.definitions.append(definition)
            self.operations.append(("definition", definition))

    def write(self, record: TraceRecord) -> None:
        with self._lock:
            self.records.append(record)
            self.operations.append(("record", record))


@dataclass(frozen=True, slots=True)
class DestinationCheckpoint:
    recordCount: int
    definitionCount: int
    operationCount: int


def checkpointDestination(
    destination: CollectingDestination,
) -> DestinationCheckpoint:
    with destination._lock:
        return DestinationCheckpoint(
            recordCount=len(destination.records),
            definitionCount=len(destination.definitions),
            operationCount=len(destination.operations),
        )


def recordsAfter(
    destination: CollectingDestination,
    checkpoint: DestinationCheckpoint,
) -> list[TraceRecord]:
    with destination._lock:
        return list(destination.records[checkpoint.recordCount:])


def definitionsAfter(
    destination: CollectingDestination,
    checkpoint: DestinationCheckpoint,
) -> list[TraceTypeDefinition]:
    with destination._lock:
        return list(destination.definitions[checkpoint.definitionCount:])


def operationsAfter(
    destination: CollectingDestination,
    checkpoint: DestinationCheckpoint,
) -> list[tuple[str, object]]:
    with destination._lock:
        return list(destination.operations[checkpoint.operationCount:])


def createSinkTracer(
    *,
    origin: str = "actant.test",
    **kwargs: object,
) -> Tracer:
    return Tracer(
        origin=origin,
        destinations=(TraceSinkDestination(),),
        **kwargs,
    )
