# file: backend/llm/llmProcessingPipelineStages.py ; version: 5
from __future__ import annotations

from dataclasses import dataclass

from backend.core.validation import requireExactNonBlankString

__all__: list[str] = [
    "BUILD_QUERY",
    "BUILD_QUERY_ITEMS",
    "FILTER_QUERY_ITEMS",
    "FINALIZE",
    "LLM_STAGE_IDS",
    "LLM_STAGE_SEQUENCE",
    "PARSE_RESPONSE",
    "PREPARE_INPUT",
    "PREPARE_PROVIDER_CALL",
    "STREAM_EVENT",
    "UPDATE_QUERY_ITEMS",
    "LlmProcessingPipelineStageId",
]


_LLM_STAGE_ID_VALUES: frozenset[str] = frozenset(
    (
        "llm.prepare-input",
        "llm.build-query-items",
        "llm.filter-query-items",
        "llm.build-query",
        "llm.prepare-provider-call",
        "llm.stream-event",
        "llm.parse-response",
        "llm.update-query-items",
        "llm.finalize",
    ),
)


@dataclass(frozen=True, slots=True)
class LlmProcessingPipelineStageId:
    """
    Identifies one stage in the built-in LLM processing lifecycle.

    Stage identifiers are closed framework identity rather than arbitrary
    extension-defined names. Every valid instance therefore corresponds to one
    stage that LlmProcessingPipeline knows how to execute.

    value is a canonical exact built-in string. It is validated rather than
    normalized, and unsupported stage identifiers are rejected.

    Equality and hashing are value-based through the frozen dataclass contract.
    """

    value: str

    def __post_init__(self) -> None:
        """Validates the canonical built-in LLM pipeline-stage identifier."""
        cleanValue = requireExactNonBlankString(self.value, "value")

        if cleanValue not in _LLM_STAGE_ID_VALUES:
            raise ValueError(
                "Unsupported LLM processing-pipeline stage; "
                f"received: {cleanValue!r}.",
            )

    def __str__(self) -> str:
        """Returns the canonical processing-pipeline stage identifier."""
        return self.value


PREPARE_INPUT = LlmProcessingPipelineStageId("llm.prepare-input")
BUILD_QUERY_ITEMS = LlmProcessingPipelineStageId("llm.build-query-items")
FILTER_QUERY_ITEMS = LlmProcessingPipelineStageId("llm.filter-query-items")
BUILD_QUERY = LlmProcessingPipelineStageId("llm.build-query")
PREPARE_PROVIDER_CALL = LlmProcessingPipelineStageId("llm.prepare-provider-call")
STREAM_EVENT = LlmProcessingPipelineStageId("llm.stream-event")
PARSE_RESPONSE = LlmProcessingPipelineStageId("llm.parse-response")
UPDATE_QUERY_ITEMS = LlmProcessingPipelineStageId("llm.update-query-items")
FINALIZE = LlmProcessingPipelineStageId("llm.finalize")


LLM_STAGE_SEQUENCE: tuple[LlmProcessingPipelineStageId, ...] = (
    PREPARE_INPUT,
    BUILD_QUERY_ITEMS,
    FILTER_QUERY_ITEMS,
    BUILD_QUERY,
    PREPARE_PROVIDER_CALL,
    STREAM_EVENT,
    PARSE_RESPONSE,
    UPDATE_QUERY_ITEMS,
    FINALIZE,
)
"""
Canonical logical order of stages in one LLM processing run.

STREAM_EVENT is executed repeatedly while provider output is streaming; its
single position in this sequence describes its lifecycle location rather than
the number of times the stage executes.
"""


LLM_STAGE_IDS: frozenset[LlmProcessingPipelineStageId] = frozenset(
    LLM_STAGE_SEQUENCE,
)
"""Complete immutable set of supported LLM processing-pipeline stage IDs."""
