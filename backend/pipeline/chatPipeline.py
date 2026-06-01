# backend/pipeline/chatPipeline.py
from __future__ import annotations

from dataclasses import dataclass, field

from backend.memory.messageStore import MessageStore
from backend.pipeline.modelProvider import ModelCompletionOutcome, ModelProvider, ModelResponse
from backend.pipeline.promptBudget import PromptTokenBudgetPolicy, PromptTokenBudgetTrimResult
from backend.pipeline.promptBuilder import PromptBuilder


class EmptyUserMessageError(ValueError):
    """Raised when the terminal chat pipeline receives an empty user message."""


@dataclass(frozen=True)
class ChatPipelineResult:
    """Turnix-facing result of one chat pipeline user message turn."""
    
    modelResponse: ModelResponse
    infoMessages: list[str] = field(default_factory=list)


class ChatPipeline:
    """Current-run chat pipeline for the terminal AppInstance."""

    def __init__(
        self,
        *,
        messageStore: MessageStore,
        promptBuilder: PromptBuilder,
        modelProvider: ModelProvider,
        promptTokenBudgetPolicy: PromptTokenBudgetPolicy | None = None,
    ) -> None:
        self.messageStore = messageStore
        self.promptBuilder = promptBuilder
        self.modelProvider = modelProvider
        self.promptTokenBudgetPolicy = promptTokenBudgetPolicy or PromptTokenBudgetPolicy()

    def runUserMessage(self, userText: str) -> ChatPipelineResult:
        normalizedText = str(userText).strip()
        if not normalizedText:
            raise EmptyUserMessageError("user message is empty")
        
        self.messageStore.appendMessage("user", normalizedText)
        modelMessages = self.promptBuilder.buildMessages(self.messageStore)
        promptTokenBudgetTrimResult = self.promptTokenBudgetPolicy.trimMessagesToBudget(modelMessages)
        
        modelResponse = self.modelProvider.generateChatResponse(promptTokenBudgetTrimResult.keptMessages)
        infoMessages = self._makeInfoMessages(modelResponse, promptTokenBudgetTrimResult)
        
        self.messageStore.appendMessage("assistant", modelResponse.content)
        return ChatPipelineResult(
            modelResponse=modelResponse,
            infoMessages=infoMessages,
        )

    def _makeInfoMessages(
        self,
        modelResponse: ModelResponse,
        promptTokenBudgetTrimResult: PromptTokenBudgetTrimResult,
    ) -> list[str]:
        infoMessages: list[str] = []
        
        promptTokenBudgetDetails = self._makePromptTokenBudgetDetails(promptTokenBudgetTrimResult)
        if promptTokenBudgetDetails:
            infoMessages.append(promptTokenBudgetDetails)
        
        outcome = modelResponse.classifyOutcome()
        
        if outcome == ModelCompletionOutcome.PARTIAL_CONTENT_HIT_TOKEN_LIMIT:
            infoMessages.append("The model stopped because it reached the token limit. The answer may be incomplete.")

        if outcome == ModelCompletionOutcome.NO_VISIBLE_CONTENT_HIT_TOKEN_LIMIT:
            if modelResponse.hasReasoningContent:
                infoMessages.append(
                    "No visible answer was produced. The model reached the token limit while generating reasoning.",
                )
            else:
                infoMessages.append(
                    "No visible answer was produced. The model reached the token limit.",
                )
        
        if outcome == ModelCompletionOutcome.EMPTY_RESPONSE:
            infoMessages.append("The model returned an empty response.")
        
        completionDetails = self._makeCompletionDetails(modelResponse)
        if completionDetails:
            infoMessages.append(completionDetails)
        
        return infoMessages
    
    def _makePromptTokenBudgetDetails(self, promptTokenBudgetTrimResult: PromptTokenBudgetTrimResult) -> str:
        if not promptTokenBudgetTrimResult.wasTrimmed:
            return ""
        
        details: list[str] = [
            "Prompt history was trimmed for this request",
            f"kept_messages={len(promptTokenBudgetTrimResult.keptMessages)}",
            f"dropped_message_count={promptTokenBudgetTrimResult.droppedMessageCount}",
        ]
        
        if promptTokenBudgetTrimResult.usedPromptTokenCount is not None:
            details.append(f"used_prompt_token_count={promptTokenBudgetTrimResult.usedPromptTokenCount}")
        
        if promptTokenBudgetTrimResult.promptTokenBudget is not None:
            details.append(f"prompt_token_budget={promptTokenBudgetTrimResult.promptTokenBudget}")
        
        if promptTokenBudgetTrimResult.remainingPromptTokenBudget is not None:
            details.append(f"remaining_prompt_token_budget={promptTokenBudgetTrimResult.remainingPromptTokenBudget}")
        
        if promptTokenBudgetTrimResult.tokenCountSource:
            details.append(f"token_count_source={promptTokenBudgetTrimResult.tokenCountSource}")
        
        return ", ".join(details)
    
    def _makeCompletionDetails(self, modelResponse: ModelResponse) -> str:
        details: list[str] = []
        
        if modelResponse.finishReason:
            details.append(f"finish_reason={modelResponse.finishReason}")
        
        if modelResponse.usage.promptTokens is not None:
            details.append(f"prompt_tokens={modelResponse.usage.promptTokens}")
        
        if modelResponse.usage.completionTokens is not None:
            details.append(f"completion_tokens={modelResponse.usage.completionTokens}")
        
        if modelResponse.usage.totalTokens is not None:
            details.append(f"total_tokens={modelResponse.usage.totalTokens}")
        
        if modelResponse.timings.wallMilliseconds is not None:
            details.append(f"wall_ms={modelResponse.timings.wallMilliseconds:.1f}")
        
        if modelResponse.timings.predictedTokensPerSecond is not None:
            details.append(f"predicted_tokens/s={modelResponse.timings.predictedTokensPerSecond:.2f}")
        
        if not details:
            return ""
        
        return ", ".join(details)
