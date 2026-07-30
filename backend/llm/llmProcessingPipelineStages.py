# file: backend/llm/llmProcessingPipelineStages.py ; version: 1
from __future__ import annotations

PREPARE_INPUT = "llm.prepare-input"
BUILD_QUERY_ITEMS = "llm.build-query-items"
FILTER_QUERY_ITEMS = "llm.filter-query-items"
BUILD_PROMPT = "llm.build-prompt"
PREPARE_ENGINE_CALL = "llm.prepare-engine-call"
STREAM_EVENT = "llm.stream-event"
PARSE_RESPONSE = "llm.parse-response"
UPDATE_QUERY_ITEMS = "llm.update-query-items"
FINALIZE = "llm.finalize"

LLM_STAGE_IDS: frozenset[str] = frozenset(
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
]
