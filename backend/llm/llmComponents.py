# file: backend/llm/llmComponents.py ; version: 3
from __future__ import annotations

from dataclasses import dataclass

from backend.core.validation import requireExactNonBlankString, typeName
from backend.llm.llmPrompt import DefaultLlmPromptBuilder, LlmPromptBuilder
from backend.llm.llmQueryItem import DefaultLlmQueryItemFilter, LlmQueryItemFilter
from backend.pack.packCodeEntry import PackCodeEntryInstanceId

__all__: list[str] = [
    "LlmBackendComponentOwner",
    "LlmStageComponentOwner",
    "LlmStageComponentRegistry",
    "LlmStageComponentsSnapshot",
    "validateComponentOwner",
    "validatePromptBuilder",
    "validateQueryItemFilter",
]


@dataclass(frozen=True, slots=True)
class LlmBackendComponentOwner:
    """Identifies one built-in backend LLM component."""

    componentId: str

    def __post_init__(self) -> None:
        """Validates the backend LLM component owner."""
        requireExactNonBlankString(self.componentId, "componentId")


type LlmStageComponentOwner = (
    LlmBackendComponentOwner
    | PackCodeEntryInstanceId
)


_DEFAULT_QUERY_ITEM_FILTER_OWNER = LlmBackendComponentOwner(
    "backend.default-query-item-filter",
)

_DEFAULT_PROMPT_BUILDER_OWNER = LlmBackendComponentOwner(
    "backend.default-prompt-builder",
)


@dataclass(frozen=True, slots=True)
class LlmStageComponentsSnapshot:
    """Represents active replaceable order-zero components for one run."""

    queryItemFilterOwner: LlmStageComponentOwner
    queryItemFilter: LlmQueryItemFilter
    promptBuilderOwner: LlmStageComponentOwner
    promptBuilder: LlmPromptBuilder

    def __post_init__(self) -> None:
        """Validates the LLM stage-components snapshot."""
        validateComponentOwner(
            self.queryItemFilterOwner,
            "queryItemFilterOwner",
        )
        validateQueryItemFilter(self.queryItemFilter)

        validateComponentOwner(
            self.promptBuilderOwner,
            "promptBuilderOwner",
        )
        validatePromptBuilder(self.promptBuilder)


class LlmStageComponentRegistry:
    """Owns the replaceable order-zero filter and prompt builder."""

    def __init__(self) -> None:
        """Initializes the registry with built-in stage components."""
        self._queryItemFilterOwner: LlmStageComponentOwner = (
            _DEFAULT_QUERY_ITEM_FILTER_OWNER
        )
        self._queryItemFilter: LlmQueryItemFilter = DefaultLlmQueryItemFilter()
        self._promptBuilderOwner: LlmStageComponentOwner = (
            _DEFAULT_PROMPT_BUILDER_OWNER
        )
        self._promptBuilder: LlmPromptBuilder = DefaultLlmPromptBuilder()

    def replaceQueryItemFilter(
        self,
        *,
        ownerId: PackCodeEntryInstanceId,
        queryItemFilter: LlmQueryItemFilter,
    ) -> None:
        """Replaces the active order-zero query-item filter."""
        if not isinstance(ownerId, PackCodeEntryInstanceId):
            raise TypeError(
                "ownerId must be a PackCodeEntryInstanceId; "
                f"got {typeName(ownerId)}.",
            )
        validateQueryItemFilter(queryItemFilter)
        self._queryItemFilterOwner = ownerId
        self._queryItemFilter = queryItemFilter

    def replacePromptBuilder(
        self,
        *,
        ownerId: PackCodeEntryInstanceId,
        promptBuilder: LlmPromptBuilder,
    ) -> None:
        """Replaces the active order-zero prompt builder."""
        if not isinstance(ownerId, PackCodeEntryInstanceId):
            raise TypeError(
                "ownerId must be a PackCodeEntryInstanceId; "
                f"got {typeName(ownerId)}.",
            )
        validatePromptBuilder(promptBuilder)
        self._promptBuilderOwner = ownerId
        self._promptBuilder = promptBuilder

    def snapshot(self) -> LlmStageComponentsSnapshot:
        """Returns an immutable snapshot of the active components."""
        return LlmStageComponentsSnapshot(
            queryItemFilterOwner=self._queryItemFilterOwner,
            queryItemFilter=self._queryItemFilter,
            promptBuilderOwner=self._promptBuilderOwner,
            promptBuilder=self._promptBuilder,
        )


def validateComponentOwner(owner: LlmStageComponentOwner, name: str) -> None:
    """Validates one LLM stage-component owner."""
    requireExactNonBlankString(name, "name")

    if not isinstance(
        owner,
        (LlmBackendComponentOwner, PackCodeEntryInstanceId),
    ):
        raise TypeError(
            f"{name} must be an LlmBackendComponentOwner or "
            "PackCodeEntryInstanceId; "
            f"got {typeName(owner)}.",
        )


def validateQueryItemFilter(queryItemFilter: LlmQueryItemFilter) -> None:
    """Validates the query-item filter contract."""
    if not callable(getattr(queryItemFilter, "filter", None)):
        raise TypeError(
            "queryItemFilter must expose callable filter(context); "
            f"got {typeName(queryItemFilter)}.",
        )


def validatePromptBuilder(promptBuilder: LlmPromptBuilder) -> None:
    """Validates the prompt-builder contract."""
    if not callable(getattr(promptBuilder, "build", None)):
        raise TypeError(
            "promptBuilder must expose callable build(context); "
            f"got {typeName(promptBuilder)}.",
        )
