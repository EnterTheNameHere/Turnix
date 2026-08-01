# file: backend/llm/llmProcessingPipeline.py ; version 3
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.core.immutableValue import ImmutableValueFreezer
from backend.core.validation import requireInstance, requireInteger, requireString, typeName
from backend.llm.errors import LlmPipelineStateError, LlmPromptBudgetError, LlmProviderProtocolError
from backend.llm.llmProcessingPipelineRun import (
    LlmProcessingRequest,
    LlmProcessingRun,
    LlmProcessingRunId,
    LlmProcessingRunResult,
    LlmResponse,
)
from backend.llm.llmProcessingPipelineStages import (
    BUILD_PROMPT,
    BUILD_QUERY_ITEMS,
    FILTER_QUERY_ITEMS,
    FINALIZE,
    PARSE_RESPONSE,
    PREPARE_ENGINE_CALL,
    PREPARE_INPUT,
    STREAM_EVENT,
    UPDATE_QUERY_ITEMS,
    LlmProcessingPipelineStageId,
)
from backend.llm.llmPrompt import LlmPromptBuilderContext
from backend.llm.llmQueryItem import LlmQueryItemFilterContext, validateUniqueQueryItemIdentities
from backend.llm.llmStageContext import LlmStageContext
from backend.llm.llmTypes import (
    LlmCallRequest,
    LlmExecutionProfile,
    LlmPromptBudget,
    LlmStreamEvent,
    LlmStreamProvider,
    LlmTokenEstimator,
)

if TYPE_CHECKING:
    from backend.llm.llmComponents import LlmStageComponentRegistry
    from backend.llm.llmHookRegistry import LlmHookPosition, LlmHookRegistry
    from backend.llm.llmProcessingPipelineTransactions import LlmPipelineTransactionManager
    from backend.llm.llmProviderRegistry import LlmStreamProviderRegistry

__all__: list[str] = [
    "ApproximateLlmTokenEstimator",
    "LlmProcessingPipeline",
]


DEFAULT_MAX_PROMPT_TOKENS = 4096


class ApproximateLlmTokenEstimator:
    """Approximates token counts when a provider has no tokenizer."""

    def estimateTokens(self, text: str) -> int:
        """Estimates the number of tokens in the provided text."""
        requireString(text, "text")
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)


class LlmProcessingPipeline:
    """Hookable, transactional, dynamically constructed LLM processing pipeline."""

    def __init__(
        self,
        *,
        providerRegistry: LlmStreamProviderRegistry,
        hookRegistry: LlmHookRegistry,
        transactionManager: LlmPipelineTransactionManager,
        componentRegistry: LlmStageComponentRegistry,
        fallbackTokenEstimator: LlmTokenEstimator | None = None,
    ) -> None:
        """Initializes the LLM processing pipeline."""
        self._providerRegistry = providerRegistry
        self._hookRegistry = hookRegistry
        self._transactionManager = transactionManager
        self._componentRegistry = componentRegistry
        self._fallbackTokenEstimator = (
            fallbackTokenEstimator
            if fallbackTokenEstimator is not None
            else ApproximateLlmTokenEstimator()
        )

    def run(self, request: LlmProcessingRequest) -> LlmProcessingRunResult:
        """Runs one complete transactional LLM processing pipeline."""
        requireInstance(request, LlmProcessingRequest, "request")

        freezer = ImmutableValueFreezer()
        rawSource = {} if request.rawInput is None else request.rawInput
        rawInput = freezer.freezeMapping(rawSource, "rawInput")
        providerOptions = freezer.freezeMapping(
            request.providerOptions,
            "providerOptions",
        )

        provider = self._providerRegistry.require(request.providerName)
        runId = LlmProcessingRunId.new()
        transaction = self._transactionManager.beginLlmPipelineTransaction(
            runId=runId,
        )
        committed = False

        def executePipeline() -> LlmProcessingRunResult:
            components = self._componentRegistry.snapshot()
            profile = provider.getExecutionProfile(
                model=request.model,
                providerOptions=providerOptions,
            )
            requireInstance(
                profile,
                LlmExecutionProfile,
                "Provider getExecutionProfile()",
            )

            tokenEstimator = (
                profile.tokenEstimator
                if profile.tokenEstimator is not None
                else self._fallbackTokenEstimator
            )
            budget = _resolveBudget(
                requested=request.promptBudget,
                profile=profile,
            )

            run = LlmProcessingRun(
                runId=runId,
                purposeId=request.purposeId,
                providerName=request.providerName,
                rawInput=rawInput,
                model=request.model,
                providerOptions=providerOptions,
                executionProfile=profile,
                budget=budget,
                tokenEstimator=tokenEstimator,
                streamObserver=request.streamObserver,
            )

            self._runSimpleStage(PREPARE_INPUT, run)
            self._runSimpleStage(BUILD_QUERY_ITEMS, run)
            validateUniqueQueryItemIdentities(run.queryItems)

            self._runHooks(FILTER_QUERY_ITEMS, "before", run)
            validateUniqueQueryItemIdentities(run.queryItems)
            run.setFilterResult(
                components.queryItemFilter.filter(
                    LlmQueryItemFilterContext(
                        queryItems=tuple(run.queryItems),
                        budget=run.budget,
                        tokenEstimator=run.tokenEstimator,
                    ),
                ),
            )
            self._runHooks(FILTER_QUERY_ITEMS, "after", run)

            self._runHooks(BUILD_PROMPT, "before", run)
            builtPrompt = components.promptBuilder.build(
                LlmPromptBuilderContext(
                    purposeId=run.purposeId,
                    rawInput=run.rawInput,
                    processedInput=tuple(run.processedInput),
                    selectedItems=tuple(run.selectedQueryItems),
                    budget=run.budget,
                ),
            )

            requireInstance(builtPrompt, str, "builtPrompt")

            run.currentPrompt = builtPrompt
            self._runHooks(BUILD_PROMPT, "after", run)
            _validatePrompt(run)

            self._runHooks(PREPARE_ENGINE_CALL, "before", run)
            run.callRequest = LlmCallRequest(
                prompt=run.currentPrompt,
                model=run.model,
                providerOptions=run.providerOptions,
            )
            self._runHooks(PREPARE_ENGINE_CALL, "after", run)

            requireInstance(run.callRequest, LlmCallRequest, "callRequest")

            self._consumeProviderStream(
                provider=provider,
                run=run,
            )

            self._runHooks(PARSE_RESPONSE, "before", run)
            run.processedResponse = "".join(run.processedResponseParts)
            self._runHooks(PARSE_RESPONSE, "after", run)

            self._runSimpleStage(UPDATE_QUERY_ITEMS, run)
            self._runSimpleStage(FINALIZE, run)

            if run.callRequest is None:
                raise LlmPipelineStateError(
                    "The completed pipeline has no call request.",
                )

            if run.processedResponse is None:
                raise LlmPipelineStateError(
                    "The completed pipeline has no processed response.",
                )

            return LlmProcessingRunResult(
                runId=runId,
                callRequest=run.callRequest,
                response=LlmResponse(
                    rawText="".join(run.rawResponseParts),
                    processedText=run.processedResponse,
                    providerMetadata=run.providerMetadata,
                    responseData=run.responseDataSnapshot(),
                ),
            )

        try:
            result = executePipeline()
            transaction.commit()

        except BaseException as pipelineError:
            try:
                transaction.rollback()
            except BaseException as rollbackError:
                raise BaseExceptionGroup(
                    "The LLM pipeline failed and its "
                    "transaction rollback also failed.",
                    [pipelineError, rollbackError],
                ) from None

            raise

        else:
            return result

    def _consumeProviderStream(
        self,
        *,
        provider: LlmStreamProvider,
        run: LlmProcessingRun,
    ) -> None:
        """Consumes and processes one provider event stream."""
        if run.callRequest is None:
            raise LlmPipelineStateError("Provider call request is missing.")

        completed = False

        stream = getattr(provider, "stream", None)
        if not callable(stream):
            raise LlmProviderProtocolError(
                "Registered provider does not expose callable stream(request).",
            )

        for eventIndex, event in enumerate(stream(run.callRequest)):
            if not isinstance(event, LlmStreamEvent):
                raise LlmProviderProtocolError(
                    "Provider yielded a non-LlmStreamEvent at index "
                    f"{eventIndex}.",
                )
            if completed:
                raise LlmProviderProtocolError(
                    "Provider emitted an event after completion.",
                )

            run.currentStreamEvent = event
            run.currentStreamText = event.text
            run.currentStreamSuppressed = False

            if event.eventType == "delta":
                run.rawResponseParts.append(event.text)

            self._runSimpleStage(STREAM_EVENT, run)

            if event.eventType == "delta":
                if not run.currentStreamSuppressed:
                    run.processedResponseParts.append(run.currentStreamText)
                    if run.streamObserver is not None:
                        run.streamObserver(
                            LlmStreamEvent(
                                eventType="delta",
                                text=run.currentStreamText,
                                metadata=event.metadata,
                            ),
                        )
                continue

            if event.eventType == "completed":
                completed = True
                run.providerMetadata = event.metadata
                if run.streamObserver is not None:
                    run.streamObserver(event)
                continue

            raise LlmProviderProtocolError(
                f"Unsupported provider event type {event.eventType!r}.",
            )

        run.currentStreamEvent = None
        run.currentStreamText = ""
        run.currentStreamSuppressed = False

        if not completed:
            raise LlmPipelineStateError(
                "Provider stream ended without a completed event.",
            )

    def _runSimpleStage(
        self,
        stageId: LlmProcessingPipelineStageId,
        run: LlmProcessingRun,
    ) -> None:
        """Runs both hook positions of a componentless pipeline stage."""
        self._runHooks(stageId, "before", run)
        self._runHooks(stageId, "after", run)

    def _runHooks(
        self,
        stageId: LlmProcessingPipelineStageId,
        position: LlmHookPosition,
        run: LlmProcessingRun,
    ) -> None:
        """Runs all registered hooks at one stage position."""
        for entry in self._hookRegistry.snapshot(
            stageId=stageId,
            position=position,
        ):
            context = LlmStageContext(
                run=run,
                stageId=stageId,
                ownerId=entry.ownerId,
                position=position,
            )

            try:
                entry.handler(context)
            finally:
                context.invalidate()


def _resolveBudget(
    *,
    requested: LlmPromptBudget | None,
    profile: LlmExecutionProfile,
) -> LlmPromptBudget:
    """Resolves the effective prompt budget against the provider limits."""
    reserved = requested.reservedResponseTokens if requested is not None else 0

    providerMaximum: int | None = None
    if profile.contextWindowTokens is not None:
        providerMaximum = profile.contextWindowTokens - reserved
        if providerMaximum < 1:
            raise LlmPromptBudgetError(
                "Reserved response tokens consume the entire context window.",
            )

    if requested is None:
        maximum = providerMaximum or DEFAULT_MAX_PROMPT_TOKENS
    elif providerMaximum is None:
        maximum = requested.maxPromptTokens
    else:
        maximum = min(requested.maxPromptTokens, providerMaximum)

    return LlmPromptBudget(
        maxPromptTokens=maximum,
        reservedResponseTokens=reserved,
    )


def _validatePrompt(run: LlmProcessingRun) -> None:
    """Validates the final prompt and its estimated token count."""
    if not isinstance(run.currentPrompt, str):
        raise LlmPipelineStateError("Prompt builder must produce a string.")
    if not run.currentPrompt.strip():
        raise LlmPipelineStateError("Prompt builder produced a blank prompt.")

    estimated = _validateTokenEstimate(
        run.tokenEstimator.estimateTokens(run.currentPrompt),
    )

    if estimated > run.budget.maxPromptTokens:
        raise LlmPromptBudgetError(
            "Final prompt exceeds the effective prompt budget.",
        )


def _validateTokenEstimate(value: int) -> int:
    """Validates one token estimate returned by an estimator."""
    try:
        estimate = requireInteger(value, "LlmTokenEstimator estimate")
    except TypeError as err:
        raise LlmPipelineStateError(
            f"LlmTokenEstimator returned a non-integer estimate; "
            f"got {typeName(value)}.",
        ) from err

    if estimate < 0:
        raise LlmPipelineStateError(
            "LlmTokenEstimator returned a negative estimate; "
            f"got {estimate}.",
        )

    return estimate
