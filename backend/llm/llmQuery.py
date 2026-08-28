# file: backend/llm/llmQuery.py ; version: 7
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer
from backend.core.validation import (
    requireBool,
    requireExactNonBlankString,
    requireInstance,
    requireInteger,
    requireNonNegativeInteger,
    typeName,
)
from backend.llm.errors import LlmPipelineStateError, LlmQueryBudgetError
from backend.llm.llmOwner import LlmOwner, requireLlmOwner
from backend.llm.llmTypes import LlmQuery, LlmQueryBudget

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from backend.llm.llmTypes import LlmTokenEstimator

__all__: list[str] = [
    "DefaultLlmQueryBuilder",
    "LlmQueryBuildContext",
    "LlmQueryBuilder",
    "LlmQueryItem",
    "LlmQueryItemId",
    "LlmQueryItemIdentity",
    "estimateLlmQueryTokens",
    "validateBuiltLlmQuery",
    "validateUniqueQueryItemIdentities",
]


_ALLOWED_QUERY_ITEM_ID_FIRST_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789",
)
_ALLOWED_QUERY_ITEM_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789._-",
)


@dataclass(frozen=True, slots=True)
class LlmQueryItemId:
    """
    Identifies one QueryItem within an LLM owner namespace for a processing
    run.

    Query-item identifiers are machine-facing identity rather than human-facing
    labels. An item's complete run-local identity is the pair of its LlmOwner
    and this identifier.

    value:

        - is an exact built-in string;
        - contains ASCII characters only;
        - starts with a lowercase ASCII letter or digit;
        - otherwise contains only lowercase ASCII letters, digits, ".", "_",
          and "-";
        - is never normalized, trimmed, or case-folded.

    The identifier need only be unique within one owner's QueryItems for the
    processing run. LlmQueryItemIdentity combines it with the owner identity to
    provide the complete run-local identity.
    """

    value: str

    def __post_init__(self) -> None:
        """Validates the canonical QueryItem identifier."""
        cleanValue = requireExactNonBlankString(self.value, "value")

        if not cleanValue.isascii():
            invalidCharacter = next(
                character
                for character in cleanValue
                if not character.isascii()
            )
            raise ValueError(
                "value must contain ASCII characters only.\n"
                f"received: {cleanValue!r}\n"
                f"invalid character: {invalidCharacter!r}",
            )

        firstCharacter = cleanValue[0]
        if firstCharacter not in _ALLOWED_QUERY_ITEM_ID_FIRST_CHARACTERS:
            raise ValueError(
                "value must start with a lowercase ASCII letter or digit.\n"
                f"received: {cleanValue!r}\n"
                f"invalid first character: {firstCharacter!r}",
            )

        for character in cleanValue[1:]:
            if character not in _ALLOWED_QUERY_ITEM_ID_CHARACTERS:
                raise ValueError(
                    f"value contains invalid character {character!r}.\n"
                    f"received: {cleanValue!r}\n"
                    "QueryItem identifiers allow only lowercase ASCII "
                    'letters, digits, ".", "_", and "-".',
                )

    def __str__(self) -> str:
        """Returns the canonical QueryItem identifier."""
        return self.value


@dataclass(frozen=True, slots=True)
class LlmQueryItemIdentity:
    """
    Identifies one QueryItem uniquely within an LLM processing run.

    ownerId identifies the backend or Pack participant that produced the item.
    itemId identifies the item within that owner's run-local namespace.

    Keeping producer identity separate from the item identifier prevents
    independently acting components from having to coordinate one global
    QueryItem identifier namespace.
    """

    ownerId: LlmOwner
    itemId: LlmQueryItemId

    def __post_init__(self) -> None:
        """Validates the structured run-local QueryItem identity."""
        requireLlmOwner(self.ownerId, "ownerId")
        requireInstance(self.itemId, LlmQueryItemId, "itemId")


@dataclass(frozen=True, slots=True)
class LlmQueryItem:
    """
    Represents one transient candidate contribution to an LLM query.

    A QueryItem describes information that may participate in one dynamically
    constructed inference query. It is not itself the final provider-facing
    query representation.

    identity provides run-local producer-qualified identity.

    contentType identifies the semantic representation contract of payload.
    Generic filtering and pipeline infrastructure treat payload as opaque.
    Query builders decide how selected content types are interpreted and
    assembled into a complete LlmQuery.

    payload is deliberately not copied, recursively frozen, serialized, or
    coerced by this class. The producer is responsible for ensuring that its
    observable meaning remains stable while the QueryItem is observable during
    the processing run.

    importance is a relative selection hint. Larger values represent greater
    preference for inclusion. It is deliberately unbounded and may be negative;
    filtering policies define how relative importance affects selection.

    mandatory requests inclusion of the item. Mandatory status does not permit
    filtering or query construction to exceed the effective query budget.

    estimatedInputTokens is optional advisory information supplied by the item
    producer. It may assist custom policies, diagnostics, or inspection, but it
    is not authoritative for query budgeting. Representation-aware selection
    cost and complete-query estimation remain authoritative.

    category is optional semantic classification available to filtering and
    other LLM-domain components. It does not affect identity.

    metadata contains recursively frozen generic auxiliary information suitable
    for deterministic framework inspection without interpreting payload.
    """

    identity: LlmQueryItemIdentity
    contentType: str
    payload: object
    importance: int
    mandatory: bool = False
    estimatedInputTokens: int | None = None
    category: str | None = None
    metadata: Mapping[str, ImmutableValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validates the QueryItem values and freezes generic metadata."""
        requireInstance(self.identity, LlmQueryItemIdentity, "identity")
        requireExactNonBlankString(self.contentType, "contentType")
        requireInteger(self.importance, "importance")
        requireBool(self.mandatory, "mandatory")

        if self.estimatedInputTokens is not None:
            requireNonNegativeInteger(
                self.estimatedInputTokens,
                "estimatedInputTokens",
            )

        if self.category is not None:
            requireExactNonBlankString(self.category, "category")

        frozenMetadata = ImmutableValueFreezer().freezeMapping(
            self.metadata,
            "metadata",
        )
        object.__setattr__(self, "metadata", frozenMetadata)


@dataclass(frozen=True, slots=True)
class LlmQueryBuildContext:
    """
    Represents the immutable input supplied to one query builder invocation.

    selectedItems contains only QueryItems that survived the filtering stage,
    in their established query order. The builder constructs the complete
    LlmQuery representation from this ordered selection.

    Query construction does not own selection policy. A builder must not add,
    remove, reorder, replace, or mutate the selected QueryItems through this
    context.
    """

    selectedItems: tuple[LlmQueryItem, ...]

    def __post_init__(self) -> None:
        """Validates the query-builder input context."""
        requireInstance(self.selectedItems, tuple, "selectedItems")

        for index, item in enumerate(self.selectedItems):
            requireInstance(item, LlmQueryItem, f"selectedItems[{index}]")

        validateUniqueQueryItemIdentities(self.selectedItems)


class LlmQueryBuilder(Protocol):
    """
    Constructs complete query representations from selected QueryItems.

    Query building and QueryItem filtering are distinct responsibilities.
    Filtering decides which candidate information participates. A builder
    interprets the already-selected QueryItems and constructs their complete
    provider-neutral LlmQuery representation.

    estimateSelectionTokens() provides the representation-aware cost capability
    required by filtering. For any ordered QueryItem selection, it must
    estimate the same representation semantics that build() would construct
    from that selection. This allows filtering to reason about complete-query
    cost without receiving query-construction authority or inspecting the
    constructed query.

    Implementations must treat supplied QueryItems and their payloads as
    read-only. For the same ordered selection and token estimator within one
    processing run, estimateSelectionTokens() must return stable results.

    build() must preserve the semantic participation and ordering of the
    selectedItems supplied through LlmQueryBuildContext.
    Representation-specific transformation is expected; changing which
    QueryItems participate is not.
    """

    def estimateSelectionTokens(
        self,
        queryItems: tuple[LlmQueryItem, ...],
        tokenEstimator: LlmTokenEstimator,
    ) -> int:
        """
        Estimates complete-query input cost for one ordered QueryItem selection.

        Args:
            queryItems:
                Proposed selected QueryItems in their intended query order.
            tokenEstimator:
                Active provider/model estimator for complete LlmQuery objects.

        Returns:
            An exact built-in non-negative integer estimating the input-token
            usage of the complete query representation build() would produce.

        """
        ...

    def build(self, context: LlmQueryBuildContext) -> LlmQuery:
        """
        Constructs one complete provider-neutral query.

        Args:
            context:
                Ordered QueryItems selected by the filtering stage.

        Returns:
            The complete LlmQuery representation for the selected items.

        """
        ...


class DefaultLlmQueryBuilder:
    """
    Builds the built-in ``text/plain`` query representation.

    Every selected QueryItem must use contentType ``text/plain`` and an exact
    built-in str payload.

    Selected payloads are retained in supplied order and joined using two
    newline characters. No additional normalization, trimming, escaping, role
    assignment, or other semantic transformation is performed.

    An empty selection produces an empty ``text/plain`` query.

    Richer query representations replace this builder rather than flattening
    structured, multimodal, or otherwise non-text content in generic pipeline
    code.
    """

    def estimateSelectionTokens(
        self,
        queryItems: tuple[LlmQueryItem, ...],
        tokenEstimator: LlmTokenEstimator,
    ) -> int:
        """
        Estimates the complete built-in plain-text query representation.

        The estimate is derived by constructing exactly the same representation
        build() would produce and applying the active complete-query token
        estimator to it.
        """
        query = self.build(LlmQueryBuildContext(selectedItems=queryItems))
        return estimateLlmQueryTokens(tokenEstimator, query)

    def build(self, context: LlmQueryBuildContext) -> LlmQuery:
        """
        Constructs a ``text/plain`` query from selected QueryItems.

        Raises:
            LlmPipelineStateError:
                If any selected QueryItem is not compatible with the built-in
                plain-text representation.

        """
        requireInstance(context, LlmQueryBuildContext, "context")

        textParts: list[str] = []

        for item in context.selectedItems:
            if (
                item.contentType != "text/plain"
                or type(item.payload) is not str
            ):
                raise LlmPipelineStateError(
                    "DefaultLlmQueryBuilder only accepts text/plain query "
                    "items with exact built-in str payloads; "
                    f"ownerId={item.identity.ownerId!r}, "
                    f"itemId={item.identity.itemId!r}, "
                    f"contentType={item.contentType!r}, "
                    f"payloadType={typeName(item.payload)}.",
                )

            textParts.append(item.payload)

        return LlmQuery(
            formatId="text/plain",
            payload="\n\n".join(textParts),
        )


def validateUniqueQueryItemIdentities(
    queryItems: Sequence[LlmQueryItem],
) -> None:
    """
    Validates that an ordered QueryItem collection contains unique identities.

    Identity uniqueness is based exclusively on LlmQueryItemIdentity. QueryItem
    equality and payload equality are deliberately not evaluated because
    payloads are opaque and may define arbitrary equality behaviour.

    Args:
        queryItems:
            QueryItems whose run-local identities must be unique.

    Raises:
        TypeError:
            If any element is not an LlmQueryItem.
        ValueError:
            If two QueryItems use the same LlmQueryItemIdentity.

    """
    seen: set[LlmQueryItemIdentity] = set()

    for index, item in enumerate(queryItems):
        requireInstance(item, LlmQueryItem, f"queryItems[{index}]")

        if item.identity in seen:
            raise ValueError(
                "Duplicate LLM query item identity; "
                f"ownerId={item.identity.ownerId!r}, "
                f"itemId={item.identity.itemId!r}.",
            )

        seen.add(item.identity)


def estimateLlmQueryTokens(
    tokenEstimator: LlmTokenEstimator,
    query: LlmQuery,
) -> int:
    """
    Returns one validated input-token estimate for a complete LlmQuery.

    The token estimator is an extension boundary. Returning anything other than
    an exact built-in non-negative integer is therefore treated as invalid LLM
    pipeline state rather than ordinary budget exhaustion.

    Args:
        tokenEstimator:
            Active provider/model complete-query token estimator.
        query:
            Complete query whose input-token usage is estimated.

    Returns:
        The exact built-in non-negative token estimate.

    Raises:
        TypeError:
            If tokenEstimator does not expose callable
            estimateInputTokens(query), or query is not an LlmQuery.
        LlmPipelineStateError:
            If the estimator returns a value that violates its runtime
            contract.

    """
    requireInstance(query, LlmQuery, "query")

    if not callable(getattr(tokenEstimator, "estimateInputTokens", None)):
        raise TypeError(
            "tokenEstimator must expose callable estimateInputTokens(query); "
            f"received {typeName(tokenEstimator)}.",
        )

    estimated = tokenEstimator.estimateInputTokens(query)

    if type(estimated) is not int:
        raise LlmPipelineStateError(
            "LlmTokenEstimator returned a non-integer estimate; "
            f"received {typeName(estimated)}.",
        )

    if estimated < 0:
        raise LlmPipelineStateError(
            "LlmTokenEstimator returned a negative estimate; "
            f"received: {estimated}.",
        )

    return estimated


def validateBuiltLlmQuery(
    *,
    query: LlmQuery,
    budget: LlmQueryBudget,
    tokenEstimator: LlmTokenEstimator,
) -> None:
    """
    Validates one complete built query against the effective input budget.

    This validation is authoritative for the complete representation actually
    produced by query construction. Selection-cost estimates used during
    filtering do not replace this final complete-query budget check.

    Args:
        query:
            Complete query to validate.
        budget:
            Effective input-token budget for the processing run.
        tokenEstimator:
            Active provider/model estimator for complete queries.

    Raises:
        TypeError:
            If query or budget has an invalid runtime type, or tokenEstimator
            does not satisfy its structural runtime contract.
        LlmPipelineStateError:
            If tokenEstimator returns an invalid estimate.
        LlmQueryBudgetError:
            If the complete built query exceeds the effective input budget.

    """
    requireInstance(query, LlmQuery, "query")
    requireInstance(budget, LlmQueryBudget, "budget")

    estimatedTokens = estimateLlmQueryTokens(tokenEstimator, query)

    if estimatedTokens > budget.maxInputTokens:
        raise LlmQueryBudgetError(
            "Constructed query exceeds the effective query budget; "
            f"estimatedInputTokens={estimatedTokens}, "
            f"maxInputTokens={budget.maxInputTokens}.",
        )
