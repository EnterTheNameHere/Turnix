# file: backend/llm/llmComponents.py ; version: 6
from __future__ import annotations

from dataclasses import dataclass, replace
from threading import Lock

from backend.core.validation import requireExactNonBlankString, requireInstance, typeName
from backend.llm.llmOwner import LlmBackendOwner, LlmOwner, requireLlmOwner
from backend.llm.llmQuery import DefaultLlmQueryBuilder, LlmQueryBuilder
from backend.llm.llmQueryFilter import DefaultLlmQueryItemFilter, LlmQueryItemFilter
from backend.pack.packCodeEntry import PackCodeEntryInstanceId

__all__: list[str] = [
    "LlmStageComponentRegistry",
    "LlmStageComponentsSnapshot",
    "requireQueryBuilder",
    "requireQueryItemFilter",
]


_DEFAULT_QUERY_ITEM_FILTER_OWNER = LlmBackendOwner(
    "backend.default-query-item-filter",
)
_DEFAULT_QUERY_BUILDER_OWNER = LlmBackendOwner(
    "backend.default-query-builder",
)


@dataclass(frozen=True, slots=True)
class LlmStageComponentsSnapshot:
    """
    Represents the complete LLM stage-component configuration for one run.

    Each component is stored together with the owner responsible for supplying
    it. A processing run obtains one snapshot before stage execution begins and
    uses that snapshot for its entire lifetime.

    The snapshot object is immutable. Component objects themselves are not
    recursively frozen; component implementations are responsible for obeying
    their own runtime contracts and for providing run-safe behaviour when
    shared across processing runs.
    """

    queryItemFilterOwner: LlmOwner
    queryItemFilter: LlmQueryItemFilter
    queryBuilderOwner: LlmOwner
    queryBuilder: LlmQueryBuilder

    def __post_init__(self) -> None:
        """Validates all owners and structural component contracts."""
        requireLlmOwner(self.queryItemFilterOwner, "queryItemFilterOwner")
        requireQueryItemFilter(self.queryItemFilter, "queryItemFilter")
        requireLlmOwner(self.queryBuilderOwner, "queryBuilderOwner")
        requireQueryBuilder(self.queryBuilder, "queryBuilder")


class LlmStageComponentRegistry:
    """
    Owns the replaceable LLM stage-component configuration.

    The registry starts with Actant's built-in query-item filter and query
    builder. Pack code may replace either component for subsequently started
    processing runs.

    Component changes do not mutate snapshots already obtained by active runs.
    snapshot() returns one coherent registry state containing matching
    component and owner identities.

    Registry mutation and snapshot acquisition are synchronized. Concurrent
    component replacement therefore cannot expose partially updated
    owner/component pairs or a partially constructed registry state.

    This synchronization protects registry configuration only. It does not make
    arbitrary query-filter or query-builder implementation thread-safe.
    """

    __slots__ = (
        "_lock",
        "_snapshot",
    )

    def __init__(self) -> None:
        """Initializes the registry with the built-in LLM stage components."""
        self._lock = Lock()
        self._snapshot = LlmStageComponentsSnapshot(
            queryItemFilterOwner=_DEFAULT_QUERY_ITEM_FILTER_OWNER,
            queryItemFilter=DefaultLlmQueryItemFilter(),
            queryBuilderOwner=_DEFAULT_QUERY_BUILDER_OWNER,
            queryBuilder=DefaultLlmQueryBuilder(),
        )

    def replaceQueryItemFilter(
        self,
        *,
        ownerId: PackCodeEntryInstanceId,
        queryItemFilter: LlmQueryItemFilter,
    ) -> None:
        """
        Replaces the query-item filter for subsequently started runs.

        Args:
            ownerId:
                Loaded Pack code-entry instance responsible for the
                replacement component.
            queryItemFilter:
                Replacement query-item filter.

        Raises:
            TypeError:
                If ownerId or queryItemFilter violates its runtime contract.

        """
        requireInstance(ownerId, PackCodeEntryInstanceId, "ownerId")
        requireQueryItemFilter(queryItemFilter, "queryItemFilter")

        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                queryItemFilterOwner=ownerId,
                queryItemFilter=queryItemFilter,
            )

    def replaceQueryBuilder(
        self,
        *,
        ownerId: PackCodeEntryInstanceId,
        queryBuilder: LlmQueryBuilder,
    ) -> None:
        """
        Replaces the query builder for subsequently started runs.

        Args:
            ownerId:
                Loaded Pack code-entry instance responsible for the
                replacement component.
            queryBuilder:
                Replacement representation-aware query builder.

        Raises:
            TypeError:
                If ownerId or queryBuilder violates its runtime contract.

        """
        requireInstance(ownerId, PackCodeEntryInstanceId, "ownerId")
        requireQueryBuilder(queryBuilder, "queryBuilder")

        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                queryBuilderOwner=ownerId,
                queryBuilder=queryBuilder,
            )

    def snapshot(self) -> LlmStageComponentsSnapshot:
        """
        Returns the coherent stage-component configuration for a new run.

        The returned snapshot is the immutable registry-state object current at
        acquisition time. Later registry replacements create new snapshot
        objects and do not modify this one.
        """
        with self._lock:
            return self._snapshot


def requireQueryItemFilter(
    queryItemFilter: LlmQueryItemFilter,
    name: str,
) -> LlmQueryItemFilter:
    """
    Validates and returns one structural LLM query-item filter.

    Runtime structural validation establishes only that the object exposes the
    callable entry point required by LlmQueryItemFilter. The processing
    pipeline validates the component's returned values separately.

    Args:
        queryItemFilter:
            Component object to validate.
        name:
            Diagnostic name used when reporting invalid input.

    Returns:
        The original validated component object unchanged.

    Raises:
        TypeError:
            If queryItemFilter does not expose callable filter(context), or
            name is invalid.
        ValueError:
            If name is blank or contains surrounding whitespace.

    """
    cleanName = requireExactNonBlankString(name, "name")

    if not callable(getattr(queryItemFilter, "filter", None)):
        raise TypeError(
            f"{cleanName} must expose callable filter(context); "
            f"received {typeName(queryItemFilter)}.",
        )

    return queryItemFilter


def requireQueryBuilder(
    queryBuilder: LlmQueryBuilder,
    name: str,
) -> LlmQueryBuilder:
    """
    Validates and returns one structural LLM query builder.

    A query builder must expose both the representation-aware selection-cost
    capability used by filtering and the query-construction entry point used by
    BUILD_QUERY stage.

    Runtime structural validation does not execute either method. Returned
    token estimates and built queries are validated separately when the
    component is used.

    Args:
        queryBuilder:
            Component object to validate.
        name:
            Diagnostic name used when reporting invalid input.

    Returns:
        The original validated component object unchanged.

    Raises:
        TypeError:
            If queryBuilder is missing a required callable member, or if name
            is invalid.
        ValueError:
            If name is blank or contains surrounding whitespace.

    """
    cleanName = requireExactNonBlankString(name, "name")

    requiredMembers = ("estimateSelectionTokens", "build")

    missingMembers = tuple(
        member
        for member in requiredMembers
        if not callable(getattr(queryBuilder, member, None))
    )

    if missingMembers:
        missingText = ", ".join(f"{member}()" for member in missingMembers)
        raise TypeError(
            f"{cleanName} must expose callable {missingText}; "
            f"received {typeName(queryBuilder)}.",
        )

    return queryBuilder
