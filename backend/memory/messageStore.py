from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


MessageRole = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ChatMessage:
    """Single current-run chat message used by the Milestone 2 terminal chat."""

    role: MessageRole
    content: str

    def toModelMessage(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class MessageStore:
    """In-memory message store for the current AppInstance run."""

    def __init__(self) -> None:
        self._messages: list[ChatMessage] = []

    def appendMessage(self, role: MessageRole, content: str) -> ChatMessage:
        message = ChatMessage(role=role, content=str(content))
        self._messages.append(message)
        return message

    def getMessages(self) -> list[ChatMessage]:
        return list(self._messages)

    def getRecentMessages(self, limit: int | None = None) -> list[ChatMessage]:
        if limit is None or limit <= 0:
            return self.getMessages()
        return list(self._messages[-limit:])

    def clear(self) -> None:
        self._messages.clear()
