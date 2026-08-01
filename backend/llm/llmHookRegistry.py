# file: backend/llm/llmHookRegistry.py ; version: 3
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from backend.core.validation import requireInstance, requireInteger, typeName
from backend.llm.llmProcessingPipelineStages import LlmProcessingPipelineStageId
from backend.llm.llmStageContext import LlmStageContext
from backend.pack.packCodeEntry import PackCodeEntryInstanceId

__all__: list[str] = [
    "LlmHookHandler",
    "LlmHookPosition",
    "LlmHookRegistrationEntry",
    "LlmHookRegistry",
]


type LlmHookPosition = Literal["before", "after"]
type LlmHookHandler = Callable[[LlmStageContext], None]


@dataclass(frozen=True, slots=True)
class LlmHookRegistrationEntry:
    """Represents one deterministically ordered LLM stage hook."""

    stageId: LlmProcessingPipelineStageId
    ownerId: PackCodeEntryInstanceId
    position: LlmHookPosition
    activationOrder: int
    registrationOrder: int
    handler: LlmHookHandler

    def __post_init__(self) -> None:
        """Validates the LLM hook registration entry."""
        requireInstance(self.stageId, LlmProcessingPipelineStageId, "stageId")
        requireInstance(self.ownerId, PackCodeEntryInstanceId, "ownerId")

        if self.position not in ("before", "after"):
            raise ValueError("position must be 'before' or 'after'.")

        requireInteger(self.activationOrder, "activationOrder")
        requireInteger(self.registrationOrder, "registrationOrder")

        if self.registrationOrder < 0:
            raise ValueError("registrationOrder must be non-negative.")

        if not callable(self.handler):
            raise TypeError(
                f"handler must be callable; got {typeName(self.handler)}.",
            )


class LlmHookRegistry:
    """Stores LLM stage hooks in activation and registration order."""

    def __init__(self) -> None:
        """Initializes an empty LLM hook registry."""
        self._entries: list[LlmHookRegistrationEntry] = []
        self._nextRegistrationOrder = 0

    def register(
        self,
        *,
        stageId: LlmProcessingPipelineStageId,
        ownerId: PackCodeEntryInstanceId,
        handler: LlmHookHandler,
        activationOrder: int,
        position: LlmHookPosition = "before",
    ) -> None:
        """Registers one deterministically ordered LLM stage hook."""
        entry = LlmHookRegistrationEntry(
            stageId=stageId,
            ownerId=ownerId,
            position=position,
            activationOrder=activationOrder,
            registrationOrder=self._nextRegistrationOrder,
            handler=handler,
        )

        self._entries.append(entry)
        self._nextRegistrationOrder += 1

    def snapshot(
        self,
        *,
        stageId: LlmProcessingPipelineStageId,
        position: LlmHookPosition,
    ) -> tuple[LlmHookRegistrationEntry, ...]:
        """Returns an ordered immutable snapshot of matching hooks."""
        requireInstance(stageId, LlmProcessingPipelineStageId, "stageId")

        if position not in ("before", "after"):
            raise ValueError("position must be 'before' or 'after'.")

        return tuple(
            sorted(
                (
                    entry
                    for entry in self._entries
                    if entry.stageId == stageId and entry.position == position
                ),
                key=lambda entry: (
                    entry.activationOrder,
                    entry.registrationOrder,
                ),
            ),
        )

