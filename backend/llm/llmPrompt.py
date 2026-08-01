# file: backend/llm/llmPrompt.py ; version: 4
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from backend.core.immutableValue import ImmutableValueFreezer
from backend.core.validation import requireExactNonBlankString, requireInstance
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
        requireInstance(self.ownerId, PackCodeEntryInstanceId, "ownerId")
        requireInstance(self.inputId, LlmProcessedInputId, "inputId")

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

        requireInstance(self.processedInput, tuple, "processedInput")

        for index, contribution in enumerate(self.processedInput):
            requireInstance(
                contribution,
                LlmProcessedInput,
                f"processedInput[{index}]",
            )

        validateUniqueProcessedInputIdentities(self.processedInput)

        requireInstance(self.selectedItems, tuple, "selectedItems")

        for index, item in enumerate(self.selectedItems):
            requireInstance(item, LlmQueryItem, f"selectedItems[{index}]")

        validateUniqueQueryItemIdentities(self.selectedItems)

        requireInstance(self.budget, LlmPromptBudget, "budget")


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
