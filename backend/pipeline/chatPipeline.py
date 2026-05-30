# backend/pipeline/chatPipeline.py
from __future__ import annotations

from dataclasses import dataclass, field

from backend.memory.messageStore import MessageStore
from backend.pipeline.modelProvider import ModelCompletionOutcome, ModelProvider, ModelResponse
from backend.pipeline.promptBuilder import PromptBuilder


class EmptyUserMessageError(ValueError):
    """Raised when the terminal chat pipeline receives an empty user message."""


@dataclass(frozen=True)
class ChatPipelineResult:
    """Turnix-facing result of one chat pipeline user message turn."""
    
    assistantText: str
    modelResponse: ModelResponse
    infoMessages: list[str] = field(default_factory=list)


class ChatPipeline:
    """Minimal current-run chat pipeline for the terminal AppInstance."""

    def __init__(
        self,
        *,
        messageStore: MessageStore,
        promptBuilder: PromptBuilder,
        modelProvider: ModelProvider,
    ) -> None:
        self.messageStore = messageStore
        self.promptBuilder = promptBuilder
        self.modelProvider = modelProvider

    def runUserMessage(self, userText: str) -> ChatPipelineResult:
        normalizedText = str(userText).strip()
        if not normalizedText:
            raise EmptyUserMessageError("user message is empty")
        
        self.messageStore.appendMessage("user", normalizedText)
        modelMessages = self.promptBuilder.buildMessages(self.messageStore)
        
        modelResponse = self.modelProvider.generateChatResponse(modelMessages)
        assistantText = self._makeAssistantText(modelResponse)
        infoMessages = self._makeInfoMessages(modelResponse)
        
        self.messageStore.appendMessage("assistant", assistantText)
        return ChatPipelineResult(
            assistantText=assistantText,
            modelResponse=modelResponse,
            infoMessages=infoMessages,
        )
    
    def _makeAssistantText(self, modelResponse: ModelResponse) -> str:
        if modelResponse.hasVisibleContent:
            return modelResponse.content.strip()
        
        outcome = modelResponse.classifyOutcome()
        if outcome == ModelCompletionOutcome.NO_VISIBLE_CONTENT_HIT_TOKEN_LIMIT:
            if modelResponse.hasReasoningContent:
                return (
                    "[No visible answer was produced. The model reached the token limit while "
                    "generating reasoning output.]"
                )
            
            return "[No visible answer was produced. The model reached the token limit.]"
        
        return "[The model returned an empty response.]"
    
    def _makeInfoMessages(self, modelResponse: ModelResponse) -> list[str]:
        infoMessages: list[str] = []
        outcome = modelResponse.classifyOutcome()
        
        if outcome == ModelCompletionOutcome.PARTIAL_CONTENT_HIT_TOKEN_LIMIT:
            infoMessages.append("The model stopped because it reached the token limit. The answer may be incomplete.")

        if outcome == ModelCompletionOutcome.NO_VISIBLE_CONTENT_HIT_TOKEN_LIMIT and modelResponse.hasReasoningContent:
            infoMessages.append("The model spent the completion budget on reasoning before producing visible content.")
        
        completionDetails = self._makeCompletionDetails(modelResponse)
        if completionDetails:
            infoMessages.append(completionDetails)
        
        return infoMessages
    
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
        
        if modelResponse.timings.predictedMilliseconds is not None:
            details.append(f"predicted_tokens/s={modelResponse.timings.predictedTokensPerSecond:.2f}")
        
        if not details:
            return ""
        
        return ", ".join(details)
