# file: backend/llm/structuredMessages.py ; version: 2
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from backend.llm.llmTypes import LlmQuery


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
    """Represents an ordered provider-neutral text conversation for one inference.

    The query payload encoding is canonical UTF-8 JSON bytes. Bytes make the
    representation storage-neutral and let existing ProcessingRun evidence retain
    the complete exact query without teaching the generic pipeline a format-specific
    object codec. Message content itself is never normalized or rewritten.
    """

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

    def toPayload(self) -> bytes:
        """Encodes this conversation to the canonical durable query payload."""
        return json.dumps(
            self.snapshot(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def toQuery(self) -> LlmQuery:
        """Builds the provider-neutral structured-message LlmQuery."""
        return LlmQuery(formatId=LLM_MESSAGES_FORMAT_ID, payload=self.toPayload())

    @classmethod
    def fromPayload(cls, value: object) -> "LlmMessages":
        """Decodes and strictly validates one canonical structured query payload."""
        if type(value) is not bytes:
            raise TypeError("Structured LLM query payload must be exact bytes.")
        try:
            decoded = json.loads(value.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise ValueError("Structured LLM query payload is not valid UTF-8 JSON.") from err
        messages = cls.fromSnapshot(decoded)
        if messages.toPayload() != value:
            raise ValueError("Structured LLM query payload is not in canonical encoding.")
        return messages

    @classmethod
    def fromSnapshot(cls, value: object) -> "LlmMessages":
        """Constructs an ordered conversation from a strict snapshot sequence."""
        if not isinstance(value, (list, tuple)) or not value:
            raise TypeError(
                "Structured LLM query payload must be a non-empty list or tuple.",
            )
        return cls(tuple(LlmMessage.fromSnapshot(message) for message in value))
