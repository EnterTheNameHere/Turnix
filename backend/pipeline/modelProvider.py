from __future__ import annotations

from typing import Protocol


class ModelProvider(Protocol):
    """Boundary for chat model providers used by ChatPipeline."""

    def generateChatResponse(self, messages: list[dict[str, str]]) -> str:
        """Returns one assistant response for the supplied OpenAI-style messages."""


class MockModelProvider:
    """Small deterministic provider used until the llama.cpp server provider is wired in."""

    def generateChatResponse(self, messages: list[dict[str, str]]) -> str:
        lastUser = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                lastUser = message.get("content", "")
                break

        if not lastUser:
            return "Mock response: no user message was provided."
        return f"Mock response: {lastUser}"
