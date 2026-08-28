# file: backend/llm/llmQueryFilter.py ; version: 3
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.core.validation import requireInstance, typeName
from backend.llm.errors import LlmPipelineStateError, LlmQueryBudgetError
from backend.llm.llmQuery import (
    LlmQueryItem,
    validateUniqueQueryItemIdentities,
)
from backend.llm.llmTypes import LlmQueryBudget

__all__: list[str] = [
    "DefaultLlmQueryItemFilter",
    "LlmQueryItemFilter",
    "LlmQueryItemFilterContext",
    "LlmQueryItemFilterResult",
    "LlmQuerySelectionCostEstimator",
    "validateLlmQueryItemFilterResult",
]


class LlmQuerySelectionCostEstimator(Protocol):
    """
    Estimates complete-query input cost for proposed QueryItem selections.

    This protocol is the filtering stage's representation-aware capability
    boundary. Filtering may ask how expensive an ordered selection would be,
    but it does not receive the resulting LlmQuery and does not gain
    query-construction authority.

    An estimate represents the complete query that the active query builder
    would produce from the supplied QueryItems in their supplied order. It must
    therefore include representation-level costs such as framing, separators,
    structured-message overhead, multimodal accounting, or other costs that
    cannot be derived correctly from individual QueryItems in isolation.

    Implementations:

        - must treat queryItems and their payloads as read-only;
        - must interpret queryItems in the supplied order;
        - must return an exact built-in non-negative integer;
        - must provide stable results for the same ordered selection during one
          processing run.

    The pipeline validates the returned runtime value before using it.
    """

    def estimateSelectionTokens(
        self,
        queryItems: tuple[LlmQueryItem, ...],
    ) -> int:
        """
        Returns complete-query input-token cost for one ordered selection.

        Args:
            queryItems:
                Existing QueryItems proposed for inclusion in the query, in
                their intended query order.

        Returns:
            An exact built-in non-negative integer estimating the complete
            query's input-token usage.

        """
        ...


@dataclass(frozen=True, slots=True)
class LlmQueryItemFilterContext:
    """
    Represents the immutable inputs supplied to one query-item filter.

    queryItems is the complete ordered candidate universe visible to the
    filtering component. Candidate order is significant and must be preserved
    independently within the selected and excluded result partitions.

    budget is the effective query budget already resolved against applicable
    provider/model constraints.

    selectionCostEstimator provides representation-aware cost information
    without exposing query construction or the constructed representation.
    """

    queryItems: tuple[LlmQueryItem, ...]
    budget: LlmQueryBudget
    selectionCostEstimator: LlmQuerySelectionCostEstimator

    def __post_init__(self) -> None:
        """Validates the query-item filtering context."""
        requireInstance(self.queryItems, tuple, "queryItems")

        for index, item in enumerate(self.queryItems):
            requireInstance(item, LlmQueryItem, f"queryItems[{index}]")

        validateUniqueQueryItemIdentities(self.queryItems)
        requireInstance(self.budget, LlmQueryBudget, "budget")

        if not callable(
            getattr(
                self.selectionCostEstimator,
                "estimateSelectionTokens",
                None,
            ),
        ):
            raise TypeError(
                "selectionCostEstimator must expose callable "
                "estimateSelectionTokens(queryItems); "
                f"received {typeName(self.selectionCostEstimator)}.",
            )


@dataclass(frozen=True, slots=True)
class LlmQueryItemFilterResult:
    """
    Represents the selected/excluded partition produced by query filtering.

    Both tuples contain existing QueryItems. This object validates invariants
    that can be established without knowing the original candidate universe:
    item types, uniqueness within each partition, and disjointness between the
    two partitions.

    Validation against the complete candidate universe, mandatory-item rules,
    ordering, and the effective query budget is performed separately by
    validateLlmQueryItemFilterResult().
    """

    selectedItems: tuple[LlmQueryItem, ...]
    excludedItems: tuple[LlmQueryItem, ...]

    def __post_init__(self) -> None:
        """Validates locally provable filter-result invariants."""
        requireInstance(self.selectedItems, tuple, "selectedItems")
        requireInstance(self.excludedItems, tuple, "excludedItems")

        for index, item in enumerate(self.selectedItems):
            requireInstance(item, LlmQueryItem, f"selectedItems[{index}]")
        for index, item in enumerate(self.excludedItems):
            requireInstance(item, LlmQueryItem, f"excludedItems[{index}]")

        validateUniqueQueryItemIdentities(self.selectedItems)
        validateUniqueQueryItemIdentities(self.excludedItems)

        selectedIdentities = {item.identity for item in self.selectedItems}
        excludedIdentities = {item.identity for item in self.excludedItems}

        if selectedIdentities & excludedIdentities:
            raise ValueError(
                "A query item must not be both selected and excluded.",
            )


class LlmQueryItemFilter(Protocol):
    """
    Selects candidate QueryItems without constructing the final query.

    A filter partitions the candidate universe supplied through
    LlmQueryItemFilterContext into selectedItems and excludedItems.

    A valid filter result:

        - accounts for every candidate exactly once;
        - contains only the original candidate instances;
        - retains every mandatory candidate;
        - preserves candidate-relative order within each partition;
        - keeps the selected set within the effective query budget.

    Filtering selects information participation. It does not transform
    QueryItems, synthesize replacements, or determine the final query
    representation.
    """

    def filter(
        self,
        context: LlmQueryItemFilterContext,
    ) -> LlmQueryItemFilterResult:
        """
        Selects and excludes candidates for one query.

        Args:
            context:
                Candidate universe, effective budget, and representation-aware
                selection-cost capability.

        Returns:
            A complete selected/excluded partition of the supplied candidates.

        """
        ...


class DefaultLlmQueryItemFilter:
    """
    Implements deterministic greedy importance-based QueryItem selection.

    All mandatory candidates are selected first. If the mandatory selection
    alone exceeds the effective budget, filtering fails with
    LlmQueryBudgetError.

    Optional candidates are then considered by descending importance. Original
    candidate order breaks equal-importance ties. Each candidate is added when
    the complete tentative selection fits the budget according to the supplied
    representation-aware cost estimator.

    A candidate that does not fit is skipped, and later candidates are still
    considered. Consequently, a lower-importance but cheaper candidate may be
    selected after a higher-importance candidate was excluded.

    This policy is intentionally greedy and deterministic. It does not claim to
    find a globally optimal combination of optional QueryItems.
    """

    def filter(
        self,
        context: LlmQueryItemFilterContext,
    ) -> LlmQueryItemFilterResult:
        """
        Selects mandatory and optional candidates under the effective budget.

        Args:
            context:
                Candidate universe, budget, and representation-aware selection
                cost capability.

        Returns:
            The complete selected/excluded candidate partition.

        Raises:
            LlmQueryBudgetError:
                If the mandatory candidate selection alone exceeds the
                effective query budget.
            LlmPipelineStateError:
                If the selection-cost capability returns an invalid estimate.

        """
        indexedItems = tuple(enumerate(context.queryItems))

        selectedIndices = {
            index
            for index, item in indexedItems
            if item.mandatory
        }

        mandatoryItems = _itemsAtIndices(indexedItems, selectedIndices)

        mandatoryTokens = _estimateSelectionTokens(context, mandatoryItems)
        if mandatoryTokens > context.budget.maxInputTokens:
            raise LlmQueryBudgetError(
                "Mandatory query items exceed the effective query budget; "
                f"estimatedInputTokens={mandatoryTokens}, "
                f"maxInputTokens={context.budget.maxInputTokens}.",
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
            tentativeIndices = selectedIndices | {index}
            tentativeItems = _itemsAtIndices(indexedItems, tentativeIndices)

            if (
                _estimateSelectionTokens(context, tentativeItems)
                <= context.budget.maxInputTokens
            ):
                selectedIndices = tentativeIndices

        return LlmQueryItemFilterResult(
            selectedItems=_itemsAtIndices(indexedItems, selectedIndices),
            excludedItems=tuple(
                item
                for index, item in indexedItems
                if index not in selectedIndices
            ),
        )


def validateLlmQueryItemFilterResult(
    *,
    result: LlmQueryItemFilterResult,
    context: LlmQueryItemFilterContext,
) -> None:
    """
    Validates one filter result against its filtering context.

    context.queryItems is the authoritative candidate universe.
    The result must:

        - account for every candidate exactly once;
        - return the original candidate instances rather than replacements;
        - retain every mandatory candidate;
        - preserve candidate-relative order within selected and excluded
          partitions;
        - keep the selected representation within the effective query budget.

    QueryItem equality is deliberately not used for identity or ordering
    validation because QueryItem payloads are opaque and may define arbitrary
    equality behaviour.

    Args:
        result:
            Filter result to validate.
        context:
            Original filtering context that produced the candidate universe,
            effective budget, and selection-cost capability.

    Raises:
        TypeError:
            If result or context has an invalid runtime type.
        LlmPipelineStateError:
            If the filter returns unknown or replacement QueryItems, omits a
            candidate, excludes a mandatory candidate, changes
            candidate-relative order, or the selection-cost capability returns
            an invalid estimate.
        LlmQueryBudgetError:
            If the selected QueryItems exceed the effective query budget.

    """
    requireInstance(result, LlmQueryItemFilterResult, "result")
    requireInstance(context, LlmQueryItemFilterContext, "context")

    candidates = context.queryItems
    validateUniqueQueryItemIdentities(candidates)

    originals = {item.identity: item for item in candidates}
    returnedItems = result.selectedItems + result.excludedItems

    for item in returnedItems:
        original = originals.get(item.identity)

        if original is None:
            raise LlmPipelineStateError(
                "The query-item filter returned an unknown query item; "
                f"ownerId={item.identity.ownerId!r}, "
                f"itemId={item.identity.itemId!r}.",
            )

        if item is not original:
            raise LlmPipelineStateError(
                "The query-item filter must return the original query-item "
                "instances; "
                f"ownerId={item.identity.ownerId!r}, "
                f"itemId={item.identity.itemId!r}.",
            )

    returnedIdentities = {item.identity for item in returnedItems}

    for candidate in candidates:
        if candidate.identity not in returnedIdentities:
            raise LlmPipelineStateError(
                "The query-item filter omitted a candidate query item; "
                f"ownerId={candidate.identity.ownerId!r}, "
                f"itemId={candidate.identity.itemId!r}.",
            )
    selectedIdentities = tuple(item.identity for item in result.selectedItems)
    selectedIdentitySet = set(selectedIdentities)

    for candidate in candidates:
        if (
            candidate.mandatory
            and candidate.identity not in selectedIdentitySet
        ):
            raise LlmPipelineStateError(
                "The query-item filter excluded a mandatory query item; "
                f"ownerId={candidate.identity.ownerId!r}, "
                f"itemId={candidate.identity.itemId!r}.",
            )

    expectedSelectedIdentities = tuple(
        candidate.identity
        for candidate in candidates
        if candidate.identity in selectedIdentitySet
    )
    if selectedIdentities != expectedSelectedIdentities:
        raise LlmPipelineStateError(
            "The query-item filter changed the relative order "
            "of selected query items.",
        )

    excludedIdentities = tuple(item.identity for item in result.excludedItems)
    excludedIdentitySet = set(excludedIdentities)

    expectedExcludedIdentities = tuple(
        candidate.identity
        for candidate in candidates
        if candidate.identity in excludedIdentitySet
    )
    if excludedIdentities != expectedExcludedIdentities:
        raise LlmPipelineStateError(
            "The query-item filter changed the relative order "
            "of excluded query items.",
        )

    estimatedTokens = _estimateSelectionTokens(context, result.selectedItems)
    if estimatedTokens > context.budget.maxInputTokens:
        raise LlmQueryBudgetError(
            "Selected query items exceed the effective query budget; "
            f"estimatedInputTokens={estimatedTokens}, "
            f"maxInputTokens={context.budget.maxInputTokens}.",
        )


def _estimateSelectionTokens(
    context: LlmQueryItemFilterContext,
    queryItems: tuple[LlmQueryItem, ...],
) -> int:
    """
    Returns one validated representation-aware token estimate.

    Invalid extension-component output is translated to
    LlmPipelineStateError because an invalid estimate represents a violation of
    the active selection-cost capability contract rather than ordinary query
    budget exhaustion.
    """
    estimated = context.selectionCostEstimator.estimateSelectionTokens(
        queryItems,
    )

    if type(estimated) is not int:
        raise LlmPipelineStateError(
            "Selection cost estimator returned a non-integer token estimate; "
            f"received {typeName(estimated)}.",
        )

    if estimated < 0:
        raise LlmPipelineStateError(
            "Selection cost estimator returned a negative token estimate; "
            f"received: {estimated}.",
        )

    return estimated


def _itemsAtIndices(
    indexedItems: tuple[tuple[int, LlmQueryItem], ...],
    indices: set[int],
) -> tuple[LlmQueryItem, ...]:
    """Returns indexed QueryItems selected by index in original run order."""
    return tuple(item for index, item in indexedItems if index in indices)

