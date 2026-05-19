from __future__ import annotations

from backend.memory.messageStore import MessageStore
from backend.pipeline.modelProvider import ModelProvider
from backend.pipeline.promptBuilder import PromptBuilder


class EmptyUserMessageError(ValueError):
    """Raised when the terminal chat pipeline receives an empty user message."""


class ChatPipeline:
    """Minimal current-run chat pipeline for the Milestone 2 terminal AppInstance."""

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

    def runUserMessage(self, userText: str) -> str:
        normalizedText = str(userText).strip()
        if not normalizedText:
            raise EmptyUserMessageError("user message is empty")

        pendingUserMessage = self.messageStore.appendMessage("user", normalizedText)
        modelMessages = self.promptBuilder.buildMessages(self.messageStore)
        assistantText = self.modelProvider.generateChatResponse(modelMessages)
        assistantText = str(assistantText).strip() or "[empty assistant response]"
        self.messageStore.appendMessage("assistant", assistantText)
        return assistantText
