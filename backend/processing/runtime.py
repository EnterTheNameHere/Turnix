from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer
from backend.core.runtimeIds import newRuntimeId
from backend.values.committed import CommittedValueTransaction

__all__ = [
    "ProcessingRun",
    "ProcessingRunOutcome",
    "ProcessingStage",
    "QueryItem",
    "plainImmutableValue",
]


def plainImmutableValue(value: ImmutableValue) -> object:
    """Converts recursively immutable runtime metadata into plain JSON-compatible material."""
    if isinstance(value, MappingABC):
        return {key: plainImmutableValue(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [plainImmutableValue(item) for item in value]
    return value


class ProcessingStage(StrEnum):
    PREPARE_INPUT = "prepare-input"
    RESOLVE_EXECUTION_PROFILE = "resolve-execution-profile"
    BUILD_QUERY_ITEMS = "build-query-items"
    FILTER_QUERY_ITEMS = "filter-query-items"
    BUILD_QUERY = "build-query"
    PREPARE_PROVIDER_CALL = "prepare-provider-call"
    STREAM_EVENT = "stream-event"
    PARSE_RESPONSE = "parse-response"
    UPDATE_QUERY_ITEMS = "update-query-items"
    FINALIZE = "finalize"


class ProcessingRunOutcome(StrEnum):
    RUNNING = "Running"
    COMPLETED = "Completed"
    FAILED = "Failed"


@dataclass(frozen=True, slots=True)
class QueryItem:
    """One immutable semantically meaningful input item participating in a ProcessingRun."""

    itemId: str
    kind: str
    content: str
    metadata: Mapping[str, ImmutableValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.itemId) is not str or not self.itemId:
            raise ValueError("QueryItem.itemId must be a non-empty string.")
        if type(self.kind) is not str or not self.kind:
            raise ValueError("QueryItem.kind must be a non-empty string.")
        if type(self.content) is not str:
            raise TypeError("QueryItem.content must be a string.")
        object.__setattr__(self, "metadata", ImmutableValueFreezer().freezeMapping(self.metadata, "metadata"))

    def snapshot(self) -> dict[str, object]:
        return {
            "itemId": self.itemId,
            "kind": self.kind,
            "content": self.content,
            "metadata": plainImmutableValue(self.metadata),
        }

    @classmethod
    def fromSnapshot(cls, value: object) -> "QueryItem":
        if not isinstance(value, dict):
            raise TypeError("QueryItem snapshot must be an object.")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, dict):
            raise TypeError("QueryItem snapshot metadata must be an object.")
        return cls(
            itemId=value.get("itemId"),
            kind=value.get("kind"),
            content=value.get("content"),
            metadata=metadata,
        )


@dataclass(slots=True)
class ProcessingRun:
    """One execution of a reusable ProcessingPipeline with its own speculative transaction."""

    pipelineId: str
    transaction: CommittedValueTransaction
    processingRunId: str = field(default_factory=newRuntimeId)
    stage: ProcessingStage = ProcessingStage.PREPARE_INPUT
    outcome: ProcessingRunOutcome = ProcessingRunOutcome.RUNNING
    queryItems: tuple[QueryItem, ...] = ()

    def enterStage(self, stage: ProcessingStage) -> None:
        if self.outcome is not ProcessingRunOutcome.RUNNING:
            raise RuntimeError("A terminal ProcessingRun cannot enter another stage.")
        self.stage = stage

    def complete(self) -> None:
        if self.outcome is not ProcessingRunOutcome.RUNNING:
            raise RuntimeError("ProcessingRun is already terminal.")
        self.outcome = ProcessingRunOutcome.COMPLETED

    def fail(self) -> None:
        if self.outcome is ProcessingRunOutcome.RUNNING:
            self.outcome = ProcessingRunOutcome.FAILED
