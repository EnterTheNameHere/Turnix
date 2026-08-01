# file: backend/llm/llmStageContext.py ; version: 3
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.core.validation import requireNonBlankString, requireString, typeName
from backend.llm.errors import LlmInputRejectedError
from backend.llm.llmProcessingPipelineRun import LlmProcessingRun
from backend.llm.llmProcessingPipelineStages import (
    BUILD_PROMPT,
    BUILD_QUERY_ITEMS,
    FILTER_QUERY_ITEMS,
    PARSE_RESPONSE,
    PREPARE_ENGINE_CALL,
    PREPARE_INPUT,
    STREAM_EVENT,
    LlmProcessingPipelineStageId,
)
from backend.llm.llmTypes import LlmCallRequest, LlmStreamEvent
from backend.pack.packCodeEntry import PackCodeEntryInstanceId

if TYPE_CHECKING:
    from collections.abc import Mapping

    from backend.core.immutableValue import ImmutableValue
    from backend.llm.llmHookRegistry import LlmHookPosition
    from backend.llm.llmPrompt import LlmProcessedInput, LlmProcessedInputId
    from backend.llm.llmQueryItem import LlmQueryItem, LlmQueryItemFilterResult, LlmQueryItemId, LlmQueryItemIdentity

__all__: list[str] = [
    "LlmStageContext",
]


class LlmStageContext:
    """
    Stage-scoped hook access to an LLM processing run.

    Read properties expose immutable snapshots. Mutation methods validate the
    active stage and inject the acting owner identity.
    """

    __slots__ = (
        "_ownerId",
        "_position",
        "_run",
        "_stageId",
        "_valid",
    )

    def __init__(
        self,
        *,
        run: LlmProcessingRun,
        stageId: LlmProcessingPipelineStageId,
        ownerId: PackCodeEntryInstanceId,
        position: LlmHookPosition,
    ) -> None:
        """Initializes one stage-scoped LLM hook context."""
        if not isinstance(run, LlmProcessingRun):
            raise TypeError(
                "run must be an LlmProcessingRun; "
                f"got {typeName(run)}.",
            )
        self._run = run

        if not isinstance(stageId, LlmProcessingPipelineStageId):
            raise TypeError(
                "stageId must be an LlmProcessingPipelineStageId; "
                f"got {typeName(stageId)}.",
            )
        self._stageId = stageId

        if not isinstance(ownerId, PackCodeEntryInstanceId):
            raise TypeError(
                "ownerId must be a PackCodeEntryInstanceId; "
                f"got {typeName(ownerId)}.",
            )
        self._ownerId = ownerId

        if position not in ("before", "after"):
            raise ValueError("position must be 'before' or 'after'.")
        self._position = position

        self._valid = True

    @property
    def stageId(self) -> LlmProcessingPipelineStageId:
        """Returns the active processing-pipeline stage identifier."""
        self._requireValid()
        return self._stageId

    @property
    def ownerId(self) -> PackCodeEntryInstanceId:
        """Returns the acting Pack code-entry instance identifier."""
        self._requireValid()
        return self._ownerId

    @property
    def position(self) -> LlmHookPosition:
        """Returns the hook position within the active stage."""
        self._requireValid()
        return self._position

    @property
    def purposeId(self) -> str:
        """Returns the processing purpose identifier."""
        self._requireValid()
        return self._run.purposeId

    @property
    def rawInput(self) -> Mapping[str, ImmutableValue]:
        """Returns the raw input as a mapping of string keys to immutable values."""
        self._requireValid()
        return self._run.rawInput

    @property
    def processedInput(self) -> tuple[LlmProcessedInput, ...]:
        """Returns the processed input as a tuple of LlmProcessedInput."""
        self._requireValid()
        return tuple(self._run.processedInput)

    @property
    def queryItems(self) -> tuple[LlmQueryItem, ...]:
        """Returns the query items as a tuple of LlmQueryItem."""
        self._requireValid()
        return tuple(self._run.queryItems)

    @property
    def selectedQueryItems(self) -> tuple[LlmQueryItem, ...]:
        """Returns the selected query items as a tuple of LlmQueryItem."""
        self._requireValid()
        return tuple(self._run.selectedQueryItems)

    @property
    def excludedQueryItems(self) -> tuple[LlmQueryItem, ...]:
        """Returns the excluded query items as a tuple of LlmQueryItem."""
        self._requireValid()
        return tuple(self._run.excludedQueryItems)

    @property
    def currentPrompt(self) -> str:
        """Returns the current prompt text."""
        self._requireValid()
        return self._run.currentPrompt

    @property
    def callRequest(self) -> LlmCallRequest | None:
        """Returns the current call request, if any."""
        self._requireValid()
        return self._run.callRequest

    @property
    def currentStreamEvent(self) -> LlmStreamEvent | None:
        """Returns the current stream event, if any."""
        self._requireValid()
        return self._run.currentStreamEvent

    @property
    def currentStreamText(self) -> str:
        """Returns the current stream text."""
        self._requireValid()
        return self._run.currentStreamText

    @property
    def rawResponse(self) -> str:
        """Returns the current raw response text."""
        self._requireValid()
        return "".join(self._run.rawResponseParts)

    @property
    def processedResponse(self) -> str:
        """Returns the current processed response text."""
        self._requireValid()

        if self._run.processedResponse is not None:
            return self._run.processedResponse

        return "".join(self._run.processedResponseParts)

    @property
    def responseData(self) -> Mapping[str, ImmutableValue]:
        """Returns the current response data as a mapping of string keys to immutable values."""
        self._requireValid()
        return self._run.responseDataSnapshot()

    def addProcessedInput(
        self,
        *,
        inputId: LlmProcessedInputId,
        value: object,
    ) -> None:
        """Adds one processed-input contribution owned by the acting code entry."""
        self._requireStage(PREPARE_INPUT)
        self._run.addProcessedInput(
            ownerId=self._ownerId,
            inputId=inputId,
            value=value,
        )

    def rejectInput(
        self,
        *,
        reason: str,
        message: str,
    ) -> None:
        """Rejects the current pipeline input with a reason and message."""
        self._requireStage(PREPARE_INPUT)
        cleanReason = requireNonBlankString(reason, "reason")
        cleanMessage = requireNonBlankString(message, "message")
        raise LlmInputRejectedError(
            cleanMessage,
            purposeId=self._run.purposeId,
            ownerId=self._ownerId,
            reason=cleanReason,
        )

    def addQueryItem(
        self,
        *,
        itemId: LlmQueryItemId,
        content: str,
        importance: int,
        mandatory: bool = False,
        estimatedTokens: int | None = None,
        category: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> LlmQueryItemIdentity:
        """Adds one query item owned by the acting code entry."""
        self._requireStage(BUILD_QUERY_ITEMS, FILTER_QUERY_ITEMS)

        if (
            self._stageId == FILTER_QUERY_ITEMS
            and self._position != "before"
        ):
            raise ValueError(
                "Query items can only be added by before-filter hooks "
                "during the filter stage.",
            )

        return self._run.addQueryItem(
            ownerId=self._ownerId,
            itemId=itemId,
            content=content,
            importance=importance,
            mandatory=mandatory,
            estimatedTokens=estimatedTokens,
            category=category,
            metadata=metadata if metadata is not None else {},
        )

    def replaceFilterResult(
        self,
        result: LlmQueryItemFilterResult,
    ) -> None:
        """Replaces the current query item filter result."""
        self._requireStage(FILTER_QUERY_ITEMS)
        if self._position != "after":
            raise ValueError(
                "Filter result can only be replaced by after-filter hooks.",
            )
        self._run.setFilterResult(result)

    def replacePrompt(self, prompt: str) -> None:
        """Replaces the current prompt text."""
        self._requireStage(BUILD_PROMPT)
        if self._position != "after":
            raise ValueError(
                "Prompt can only be replaced by after-builder hooks.",
            )
        requireString(prompt, "prompt")
        self._run.currentPrompt = prompt

    def replaceCallRequest(self, request: LlmCallRequest) -> None:
        """Replaces the current call request."""
        self._requireStage(PREPARE_ENGINE_CALL)
        if self._position != "after":
            raise ValueError(
                "Call request can only be replaced by after-builder hooks.",
            )
        if not isinstance(request, LlmCallRequest):
            raise TypeError(
                "request must be an LlmCallRequest; "
                f"got {typeName(request)}.",
            )
        self._run.callRequest = request

    def replaceCurrentStreamText(self, text: str) -> None:
        """Replaces the current stream text."""
        self._requireStage(STREAM_EVENT)
        requireString(text, "text")
        self._run.currentStreamText = text

    def suppressCurrentStreamText(self) -> None:
        """Suppresses the current stream text."""
        self._requireStage(STREAM_EVENT)
        self._run.currentStreamSuppressed = True

    def replaceProcessedResponse(self, text: str) -> None:
        """Replaces the final processed response text."""
        self._requireStage(PARSE_RESPONSE)
        if self._position != "after":
            raise ValueError(
                "Processed response can only be replaced by after-parser hooks.",
            )
        requireString(text, "text")
        self._run.processedResponse = text

    def setResponseData(self, key: str, value: object) -> None:
        """Sets one response data value owned by the acting code entry."""
        self._requireStage(PARSE_RESPONSE)
        self._run.setResponseData(
            ownerId=self._ownerId,
            key=key,
            value=value,
        )

    def invalidate(self) -> None:
        """Invalidates the context after its hook invocation completes."""
        self._valid = False

    def _requireValid(self) -> None:
        """
        Raises RuntimeError if the stage context is no longer valid.

        Used to prevent access after the hook invocation has completed.
        """
        if not self._valid:
            raise RuntimeError("LLM stage context is no longer valid.")

    def _requireStage(self, *allowedStageIds: LlmProcessingPipelineStageId) -> None:
        """
        Raises ValueError if the active stage is not in the provided allowed ids.

        Also raises RuntimeError if the stage context is no longer valid.
        """
        self._requireValid()
        if self._stageId not in allowedStageIds:
            raise ValueError(
                f"Operation is not available during stage {self._stageId!r}.",
            )
