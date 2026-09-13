# file: backend/llm/structuredMessages.py ; version: 1
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


__all__ = [
    "LLM_MESSAGES_FORMAT_ID",
    "LlmMessage",
    "LlmMessages",
]


LLM_MESSAGES_FORMAT_ID = "actant.llm.messages@1"

type LlmMessageRole = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class LlmMessage:
    """Represents one exact text message in provider-neutral conversation order."""

    role: LlmMessageRole
    content: str

    def __post_init__(self) -> None:
        """Validates the role contract while preserving message text exactly."""
        if type(self.role) is not str:
            raise TypeError("LlmMessage.role must be an exact string.")
        if self.role not in {"user", "assistant"}:
            raise ValueError(f"Unsupported LLM message role {self.role!r}.")
        if type(self.content) is not str:
            raise TypeError("LlmMessage.content must be an exact string.")

    def snapshot(self) -> dict[str, str]:
        """Returns the exact JSON-compatible representation of this message."""
        return {"role": self.role, "content": self.content}

    @classmethod
    def fromSnapshot(cls, value: object) -> "LlmMessage":
        """Constructs one message from its strict provider-neutral snapshot."""
        if not isinstance(value, dict):
            raise TypeError("LLM message snapshot must be an object.")
        if set(value) != {"role", "content"}:
            raise ValueError(
                "LLM message snapshot must contain exactly role and content.",
            )
        return cls(role=value["role"], content=value["content"])


@dataclass(frozen=True, slots=True)
class LlmMessages:
    """Represents an ordered provider-neutral text conversation for one inference."""

    messages: tuple[LlmMessage, ...]

    def __post_init__(self) -> None:
        """Requires a non-empty immutable sequence of validated LLM messages."""
        if type(self.messages) is not tuple or not self.messages:
            raise ValueError("LlmMessages.messages must be a non-empty tuple.")
        for index, message in enumerate(self.messages):
            if not isinstance(message, LlmMessage):
                raise TypeError(
                    f"LlmMessages.messages[{index}] must be an LlmMessage.",
                )

    def snapshot(self) -> list[dict[str, str]]:
        """Returns ordered exact message snapshots without text normalization."""
        return [message.snapshot() for message in self.messages]

    @classmethod
    def fromSnapshot(cls, value: object) -> "LlmMessages":
        """Constructs an ordered conversation from a strict snapshot sequence."""
        if not isinstance(value, (list, tuple)) or not value:
            raise TypeError(
                "Structured LLM query payload must be a non-empty list or tuple.",
            )
        return cls(tuple(LlmMessage.fromSnapshot(message) for message in value))
