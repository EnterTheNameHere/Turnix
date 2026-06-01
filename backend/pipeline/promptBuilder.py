# backend/pipeline/promptBuilder.py
from __future__ import annotations

from backend.memory.messageStore import ChatMessage, MessageStore


class PromptBuilder:
    """Builds OpenAI-style chat messages from current-run memory."""

    def __init__(self, *, recentMessageLimit: int = 12) -> None:
        self.recentMessageLimit = recentMessageLimit

    def buildMessages(
        self,
        messageStore: MessageStore,
        pendingUserMessage: ChatMessage | None = None,
    ) -> list[dict[str, str]]:
        messages = [message.toModelMessage() for message in messageStore.getRecentMessages(self.recentMessageLimit)]
        if pendingUserMessage is not None:
            messages.append(pendingUserMessage.toModelMessage())
        return messages
