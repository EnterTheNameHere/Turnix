# file: backend/llm/llmPrompt.py ; version: 4
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from backend.core.immutableValue import ImmutableValueFreezer
from backend.core.validation import requireExactNonBlankString, typeName
from backend.llm.llmQueryItem import LlmQueryItem, validateUniqueQueryItemIdentities
from backend.llm.llmTypes import LlmPromptBudget
from backend.pack.packCodeEntry import PackCodeEntryInstanceId

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from backend.core.immutableValue import ImmutableValue

__all__: list[str] = [
    "DefaultLlmPromptBuilder",
    "LlmProcessedInput",
    "LlmProcessedInputId",
    "LlmPromptBuilder",
    "LlmPromptBuilderContext",
    "validateUniqueProcessedInputIdentities",
]


@dataclass(frozen=True, slots=True)
class LlmProcessedInputId:
    """Represents an identifier assigned to one processed-input contribution."""

    value: str

    def __post_init__(self) -> None:
        """Validates the processed-input identifier."""
        requireExactNonBlankString(self.value, "value")

    def __str__(self) -> str:
        """Returns the underlying processed-input identifier."""
        return self.value


@dataclass(frozen=True, slots=True)
class LlmProcessedInput:
    """Represents one owned processed-input contribution."""

    ownerId: PackCodeEntryInstanceId
    inputId: LlmProcessedInputId
    value: ImmutableValue

    def __post_init__(self) -> None:
        """Validates the processed-input contribution."""
        if not isinstance(self.ownerId, PackCodeEntryInstanceId):
            raise TypeError(
                "ownerId must be a PackCodeEntryInstanceId; "
                f"got {typeName(self.ownerId)}.",
            )

        if not isinstance(self.inputId, LlmProcessedInputId):
            raise TypeError(
                "inputId must be an LlmProcessedInputId; "
                f"got {typeName(self.inputId)}.",
            )

        frozenValue = ImmutableValueFreezer().freeze(self.value, "value")
        object.__setattr__(self, "value", frozenValue)


@dataclass(frozen=True, slots=True)
class LlmPromptBuilderContext:
    """Immutable input supplied to the active prompt builder."""

    purposeId: str
    rawInput: Mapping[str, ImmutableValue]
    processedInput: tuple[LlmProcessedInput, ...]
    selectedItems: tuple[LlmQueryItem, ...]
    budget: LlmPromptBudget

    def __post_init__(self) -> None:
        """Validates and freezes the prompt-builder context."""
        requireExactNonBlankString(self.purposeId, "purposeId")

        frozenRawInput = ImmutableValueFreezer().freezeMapping(
            self.rawInput,
            "rawInput",
        )
        object.__setattr__(self, "rawInput", frozenRawInput)

        if not isinstance(self.processedInput, tuple):
            raise TypeError(
                "processedInput must be a tuple; "
                f"got {typeName(self.processedInput)}.",
            )

        for index, contribution in enumerate(self.processedInput):
            if not isinstance(contribution, LlmProcessedInput):
                raise TypeError(
                    f"processedInput[{index}] must be an LlmProcessedInput; "
                    f"got {typeName(contribution)}.",
                )

        validateUniqueProcessedInputIdentities(self.processedInput)

        if not isinstance(self.selectedItems, tuple):
            raise TypeError(
                "selectedItems must be a tuple; "
                f"got {typeName(self.selectedItems)}.",
            )

        for index, item in enumerate(self.selectedItems):
            if not isinstance(item, LlmQueryItem):
                raise TypeError(
                    f"selectedItems[{index}] must be an LlmQueryItem; "
                    f"got {typeName(item)}.",
                )

        validateUniqueQueryItemIdentities(self.selectedItems)

        if not isinstance(self.budget, LlmPromptBudget):
            raise TypeError(
                "budget must be an LlmPromptBudget; "
                f"got {typeName(self.budget)}.",
            )


class LlmPromptBuilder(Protocol):
    """Defines the contract for replaceable prompt builders."""

    def build(self, context: LlmPromptBuilderContext) -> str:
        """Builds a prompt from the supplied pipeline context."""
        ...


class DefaultLlmPromptBuilder:
    """Joins selected prompt-ready item content with blank lines."""

    def build(self, context: LlmPromptBuilderContext) -> str:
        """Builds the default prompt from selected query items."""
        return "\n\n".join(
            item.content
            for item in context.selectedItems
        )


def validateUniqueProcessedInputIdentities(
    processedInput: Sequence[LlmProcessedInput],
) -> None:
    """Validates that processed-input identities are unique within the run."""
    seen: set[tuple[PackCodeEntryInstanceId, LlmProcessedInputId]] = set()

    for contribution in processedInput:
        identity = (
            contribution.ownerId,
            contribution.inputId,
        )

        if identity in seen:
            raise ValueError(
                "Duplicate processed-input identity: "
                f"ownerId={contribution.ownerId!r}, "
                f"inputId={contribution.inputId!r}.",
            )

        seen.add(identity)
