# file: backend/llm/llmProcessingPipelineStages.py ; version: 3
from __future__ import annotations

from dataclasses import dataclass

from backend.core.validation import requireExactNonBlankString

__all__: list[str] = [
    "BUILD_PROMPT",
    "BUILD_QUERY_ITEMS",
    "FILTER_QUERY_ITEMS",
    "FINALIZE",
    "LLM_STAGE_IDS",
    "PARSE_RESPONSE",
    "PREPARE_ENGINE_CALL",
    "PREPARE_INPUT",
    "STREAM_EVENT",
    "UPDATE_QUERY_ITEMS",
    "LlmProcessingPipelineStageId",
]


_LLM_STAGE_ID_VALUES: frozenset[str] = frozenset(
    (
        "llm.prepare-input",
        "llm.build-query-items",
        "llm.filter-query-items",
        "llm.build-prompt",
        "llm.prepare-engine-call",
        "llm.stream-event",
        "llm.parse-response",
        "llm.update-query-items",
        "llm.finalize",
    ),
)


@dataclass(frozen=True, slots=True)
class LlmProcessingPipelineStageId:
    """Represents a validated LLM processing-pipeline stage identifier."""

    value: str

    def __post_init__(self) -> None:
        """Validates the LLM processing-pipeline stage identifier."""
        requireExactNonBlankString(self.value, "value")

        if self.value not in _LLM_STAGE_ID_VALUES:
            raise ValueError(
                f"Unsupported LLM processing-pipeline stage {self.value!r}.",
            )

    def __str__(self) -> str:
        """Returns the underlying stage identifier."""
        return self.value


PREPARE_INPUT = LlmProcessingPipelineStageId("llm.prepare-input")
BUILD_QUERY_ITEMS = LlmProcessingPipelineStageId("llm.build-query-items")
FILTER_QUERY_ITEMS = LlmProcessingPipelineStageId("llm.filter-query-items")
BUILD_PROMPT = LlmProcessingPipelineStageId("llm.build-prompt")
PREPARE_ENGINE_CALL = LlmProcessingPipelineStageId("llm.prepare-engine-call")
STREAM_EVENT = LlmProcessingPipelineStageId("llm.stream-event")
PARSE_RESPONSE = LlmProcessingPipelineStageId("llm.parse-response")
UPDATE_QUERY_ITEMS = LlmProcessingPipelineStageId("llm.update-query-items")
FINALIZE = LlmProcessingPipelineStageId("llm.finalize")


LLM_STAGE_IDS: frozenset[LlmProcessingPipelineStageId] = frozenset(
    (
        PREPARE_INPUT,
        BUILD_QUERY_ITEMS,
        FILTER_QUERY_ITEMS,
        BUILD_PROMPT,
        PREPARE_ENGINE_CALL,
        STREAM_EVENT,
        PARSE_RESPONSE,
        UPDATE_QUERY_ITEMS,
        FINALIZE,
    ),
)
