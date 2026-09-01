# file: backend/llm/streamingRuntime.py ; version: 2
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.core.immutableValue import ImmutableValue
from backend.llm.errors import LlmProviderProtocolError
from backend.llm.llmTypes import LlmCallRequest, LlmQuery, LlmStreamEvent, LlmStreamProvider
from backend.registration import RegistrationRegistry, RegistrationScope

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = ["LlmProviderRegistry", "StreamingLlmPipeline", "StreamingLlmResult"]


class LlmProviderRegistry:
    """Published registry of provider-neutral streaming LLM providers."""

    def __init__(self) -> None:
        self._registry: RegistrationRegistry[LlmStreamProvider] = RegistrationRegistry()

    def register(self, scope: RegistrationScope, *, ownerId: str, name: str, provider: LlmStreamProvider) -> None:
        if not callable(getattr(provider, "stream", None)) or not callable(getattr(provider, "getExecutionProfile", None)):
            raise TypeError("provider must satisfy the LlmStreamProvider contract.")
        scope.register(self._registry, ownerId=ownerId, name=name, value=provider)

    def require(self, name: str) -> LlmStreamProvider:
        return self._registry.require(name).value

    def unregisterOwnedBy(self, ownerId: str) -> None:
        self._registry.unregisterOwnedBy(ownerId)


@dataclass(frozen=True, slots=True)
class StreamingLlmResult:
    """Completed streamed inference evidence retained by callers for provenance."""

    query: LlmQuery
    model: str | None
    providerName: str
    providerOptions: Mapping[str, ImmutableValue]
    rawText: str
    providerMetadata: Mapping[str, ImmutableValue] = field(default_factory=dict)


class StreamingLlmPipeline:
    """Minimal provider-neutral streaming execution path with strict completion semantics."""

    def __init__(self, *, providers: LlmProviderRegistry) -> None:
        self._providers = providers

    def run(self, *, providerName: str, query: LlmQuery, model: str | None = None,
            providerOptions: Mapping[str, ImmutableValue] | None = None,
            streamObserver: Callable[[LlmStreamEvent], None] | None = None) -> StreamingLlmResult:
        provider = self._providers.require(providerName)
        options = {} if providerOptions is None else providerOptions
        request = LlmCallRequest(query=query, model=model, providerOptions=options)
        parts: list[str] = []
        completed = False
        finalMetadata: Mapping[str, ImmutableValue] = {}
        for index, event in enumerate(provider.stream(request)):
            if not isinstance(event, LlmStreamEvent):
                raise LlmProviderProtocolError(f"Provider yielded non-LlmStreamEvent at index {index}.")
            if completed:
                raise LlmProviderProtocolError("Provider emitted an event after completion.")
            if event.eventType == "delta":
                parts.append(event.text)
            elif event.eventType == "completed":
                completed = True
                finalMetadata = event.metadata
            else:
                raise LlmProviderProtocolError(f"Unsupported provider event {event.eventType!r}.")
            if streamObserver is not None:
                streamObserver(event)
        if not completed:
            raise LlmProviderProtocolError("Provider stream ended without a completed event.")
        return StreamingLlmResult(query=query, model=model, providerName=providerName, providerOptions=options,
                                  rawText="".join(parts), providerMetadata=finalMetadata)
