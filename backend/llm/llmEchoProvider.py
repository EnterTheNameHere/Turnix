# file: backend/llm/llmEchoProvider.py ; version 2
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.core.validation import requireInstance
from backend.llm.llmTypes import LlmCallRequest, LlmExecutionProfile, LlmStreamEvent, LlmStreamProvider

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from backend.core.immutableValue import ImmutableValue

__all__: list[str] = [
    "LlmEchoProvider",
]


class LlmEchoProvider(LlmStreamProvider):
    """
    A simple LLM provider that echoes back the input.

    Used mainly for pipeline and integration tests.
    """

    def getExecutionProfile(
        self,
        *,
        model: str | None,
        providerOptions: Mapping[str, ImmutableValue],
    ) -> LlmExecutionProfile:
        """Returns the default execution profile used by the echo provider."""
        del model, providerOptions
        return LlmExecutionProfile()

    def stream(self, request: LlmCallRequest) -> Iterator[LlmStreamEvent]:
        """Yields the request prompt followed by a completion event."""
        requireInstance(request, LlmCallRequest, "request")

        yield LlmStreamEvent(
            eventType="delta",
            text=request.prompt,
        )
        yield LlmStreamEvent(
            eventType="completed",
            metadata={"provider": "echo"},
        )
