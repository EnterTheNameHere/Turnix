# file: backend/llm/llmQueryItem.py ; version: 3
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer
from backend.core.validation import requireBool, requireExactNonBlankString, requireInteger, requireString, typeName
from backend.llm.errors import LlmPipelineStateError, LlmPromptBudgetError
from backend.llm.llmTypes import LlmPromptBudget

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from backend.llm.llmTypes import LlmTokenEstimator

__all__: list[str] = [
    "DefaultLlmQueryItemFilter",
    "LlmQueryItem",
    "LlmQueryItemFilter",
    "LlmQueryItemFilterContext",
    "LlmQueryItemFilterResult",
    "LlmQueryItemIdentity",
    "validateUniqueQueryItemIdentities",
]


@dataclass(frozen=True, slots=True)
class LlmQueryItemIdentity:
    """Represents the structured run-local identity of one query item."""

    ownerId: str
    itemId: str

    def __post_init__(self) -> None:
        """Validates the query-item identity."""
        requireExactNonBlankString(self.ownerId, "ownerId")
        requireExactNonBlankString(self.itemId, "itemId")


@dataclass(frozen=True, slots=True)
class LlmQueryItem:
    """
    Represents one prompt-ready candidate contribution.

    estimatedTokens is an advisory cached estimate. The active run estimator
    remains authoritative.
    """

    identity: LlmQueryItemIdentity
    content: str
    importance: int
    mandatory: bool = False
    estimatedTokens: int | None = None
    category: str | None = None
    metadata: Mapping[str, ImmutableValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validates and freezes the query-item values."""
        if not isinstance(self.identity, LlmQueryItemIdentity):
            raise TypeError("identity must be an LlmQueryItemIdentity.")
        requireString(self.content, "content")
        requireInteger(self.importance, "importance")
        requireBool(self.mandatory, "mandatory")

        if self.estimatedTokens is not None:
            requireInteger(self.estimatedTokens, "estimatedTokens")
            if self.estimatedTokens < 0:
                raise ValueError("estimatedTokens must not be negative.")

        if self.category is not None:
            requireExactNonBlankString(self.category, "category")

        frozen = ImmutableValueFreezer().freezeMapping(
            self.metadata,
            "metadata",
        )
        object.__setattr__(self, "metadata", frozen)


@dataclass(frozen=True, slots=True)
class LlmQueryItemFilterContext:
    """Represents immutable input supplied to the active query-item filter."""

    queryItems: tuple[LlmQueryItem, ...]
    budget: LlmPromptBudget
    tokenEstimator: LlmTokenEstimator

    def __post_init__(self) -> None:
        """Validates the query-item filter context."""
        if not isinstance(self.queryItems, tuple):
            raise TypeError(
                f"queryItems must be a tuple; got {typeName(self.queryItems)}.",
            )

        for index, item in enumerate(self.queryItems):
            if not isinstance(item, LlmQueryItem):
                raise TypeError(
                    f"queryItems[{index}] must be an LlmQueryItem; "
                    f"got {typeName(item)}.",
                )

        validateUniqueQueryItemIdentities(self.queryItems)

        if not isinstance(self.budget, LlmPromptBudget):
            raise TypeError(
                "budget must be an LlmPromptBudget; "
                f"got {typeName(self.budget)}.",
            )

        if not callable(
            getattr(self.tokenEstimator, "estimateTokens", None),
        ):
            raise TypeError(
                "tokenEstimator must expose a callable estimateTokens(text).",
            )


@dataclass(frozen=True, slots=True)
class LlmQueryItemFilterResult:
    """Selected and excluded items produced by a query-item filter."""

    selectedItems: tuple[LlmQueryItem, ...]
    excludedItems: tuple[LlmQueryItem, ...]

    def __post_init__(self) -> None:
        """Validates the query-item filter result."""
        if not isinstance(self.selectedItems, tuple):
            raise TypeError(
                "selectedItems must be a tuple; "
                f"got {typeName(self.selectedItems)}.",
            )

        if not isinstance(self.excludedItems, tuple):
            raise TypeError(
                "excludedItems must be a tuple; "
                f"got {typeName(self.excludedItems)}.",
            )

        for index, item in enumerate(self.selectedItems):
            if not isinstance(item, LlmQueryItem):
                raise TypeError(
                    f"selectedItems[{index}] must be an LlmQueryItem; "
                    f"got {typeName(item)}.",
                )

        for index, item in enumerate(self.excludedItems):
            if not isinstance(item, LlmQueryItem):
                raise TypeError(
                    f"excludedItems[{index}] must be an LlmQueryItem; "
                    f"got {typeName(item)}.",
                )

        validateUniqueQueryItemIdentities(self.selectedItems)
        validateUniqueQueryItemIdentities(self.excludedItems)

        selectedIdentities = {
            item.identity
            for item in self.selectedItems
        }
        excludedIdentities = {
            item.identity
            for item in self.excludedItems
        }
        overlappingIdentities = selectedIdentities & excludedIdentities

        if overlappingIdentities:
            identity = min(
                overlappingIdentities,
                key=lambda item: (item.ownerId, item.itemId),
            )
            raise ValueError(
                f"A query item must not be both selected and excluded; "
                f"ownerId={identity.ownerId!r}, "
                f"itemId={identity.itemId!r}.",
            )

        # TODO: The pipeline must verify the context-dependent invariants:
        #       - every candidate appears exactly once;
        #       - final selection fits the budget.


class LlmQueryItemFilter(Protocol):
    """Defines the contract for replaceable query-item filters."""

    def filter(
        self,
        context: LlmQueryItemFilterContext,
    ) -> LlmQueryItemFilterResult:
        """Filters candidate query items under the supplied prompt budget."""
        ...


class DefaultLlmQueryItemFilter:
    """Selects mandatory and high-importance items under a token budget."""

    def filter(
        self,
        context: LlmQueryItemFilterContext,
    ) -> LlmQueryItemFilterResult:
        """Selects query items using the default deterministic policy."""
        indexedItems = tuple(enumerate(context.queryItems))
        selectedIndices: set[int] = {
            index
            for index, item in indexedItems
            if item.mandatory
        }

        if _estimateSelectionTokens(
            indexedItems,
            selectedIndices,
            context.tokenEstimator,
        ) > context.budget.maxPromptTokens:
            raise LlmPromptBudgetError(
                "Mandatory query items exceed the prompt budget.",
            )

        optionalItems = sorted(
            (
                (index, item)
                for index, item in indexedItems
                if not item.mandatory
            ),
            key=lambda entry: (-entry[1].importance, entry[0]),
        )

        for index, _item in optionalItems:
            tentative = selectedIndices | {index}
            tokenCount = _estimateSelectionTokens(
                indexedItems,
                tentative,
                context.tokenEstimator,
            )
            if tokenCount <= context.budget.maxPromptTokens:
                selectedIndices = tentative

        selected = tuple(
            item
            for index, item in indexedItems
            if index in selectedIndices
        )
        excluded = tuple(
            item
            for index, item in indexedItems
            if index not in selectedIndices
        )

        return LlmQueryItemFilterResult(
            selectedItems=selected,
            excludedItems=excluded,
        )


def validateUniqueQueryItemIdentities(
    queryItems: Sequence[LlmQueryItem],
) -> None:
    """Validates that query-item identities are unique within the run."""
    seen: set[LlmQueryItemIdentity] = set()

    for item in queryItems:
        if item.identity in seen:
            raise ValueError(
                "Duplicate LLM query item identity: "
                f"ownerId={item.identity.ownerId!r}, "
                f"itemId={item.identity.itemId!r}.",
            )
        seen.add(item.identity)


def _estimateSelectionTokens(
    indexedItems: Sequence[tuple[int, LlmQueryItem]],
    selectedIndices: set[int],
    tokenEstimator: LlmTokenEstimator,
) -> int:
    """Estimates the prompt tokens used by the selected query items."""
    content = "\n\n".join(
        item.content
        for index, item in indexedItems
        if index in selectedIndices
    )

    estimated = tokenEstimator.estimateTokens(content)

    try:
        estimated = requireInteger(estimated, "LlmTokenEstimator estimate")
    except TypeError as err:
        raise LlmPipelineStateError(
            "LlmTokenEstimator returned a non-integer estimate; "
            f"got {typeName(estimated)}.",
        ) from err

    if estimated < 0:
        raise LlmPipelineStateError(
            "LlmTokenEstimator returned a negative estimate; "
            f"got {estimated}.",
        )

    return estimated
