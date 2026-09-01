# file: backend/context/codeEntryContext.py ; version: 3
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from backend.capabilities.runtime import CapabilityHandler, CapabilityRegistry
    from backend.core.immutableValue import ImmutableValue
    from backend.io.managedIo import ManagedIo
    from backend.llm.llmTypes import LlmQuery, LlmStreamEvent, LlmStreamProvider
    from backend.llm.streamingRuntime import LlmProviderRegistry, StreamingLlmPipeline, StreamingLlmResult
    from backend.registration import RegistrationScope

__all__ = ["CodeEntryContext", "CodeEntryIdentity"]


class _IoFacade:
    def __init__(self, *, io: ManagedIo, requireValid: Callable[[], None]) -> None:
        self._io = io
        self._requireValid = requireValid

    def readText(self, path):
        self._requireValid()
        return self._io.readText(path)

    def readJson(self, path):
        self._requireValid()
        return self._io.readJson(path)

    def readLines(self, path):
        self._requireValid()
        return self._io.readLines(path)

    def writeTextAtomic(self, path, text: str) -> None:
        self._requireValid()
        self._io.writeTextAtomic(path, text)

    def writeJsonAtomic(self, path, value: object) -> None:
        self._requireValid()
        self._io.writeJsonAtomic(path, value)


class _CapabilityFacade:
    def __init__(self, *, ownerId: str, registry: CapabilityRegistry, scope: RegistrationScope,
                 invoker: Callable[[str, object | None], object], requireValid: Callable[[], None]) -> None:
        self._ownerId = ownerId
        self._registry = registry
        self._scope = scope
        self._invoker = invoker
        self._requireValid = requireValid

    def register(self, capabilityId: str, handler: CapabilityHandler) -> None:
        self._requireValid()
        self._registry.register(self._scope, ownerId=self._ownerId, capabilityId=capabilityId, handler=handler)

    def call(self, capabilityId: str, payload: object | None = None) -> object:
        self._requireValid()
        return self._invoker(capabilityId, payload)


class _LlmFacade:
    def __init__(self, *, ownerId: str, registry: LlmProviderRegistry, scope: RegistrationScope,
                 pipeline: StreamingLlmPipeline, requireValid: Callable[[], None]) -> None:
        self._ownerId = ownerId
        self._registry = registry
        self._scope = scope
        self._pipeline = pipeline
        self._requireValid = requireValid

    def registerProvider(self, name: str, provider: LlmStreamProvider) -> None:
        self._requireValid()
        self._registry.register(self._scope, ownerId=self._ownerId, name=name, provider=provider)

    def run(self, *, providerName: str, query: LlmQuery, model: str | None = None,
            providerOptions: Mapping[str, ImmutableValue] | None = None,
            streamObserver: Callable[[LlmStreamEvent], None] | None = None) -> StreamingLlmResult:
        self._requireValid()
        return self._pipeline.run(
            providerName=providerName,
            query=query,
            model=model,
            providerOptions=providerOptions,
            streamObserver=streamObserver,
        )


@dataclass(frozen=True, slots=True)
class CodeEntryIdentity:
    applicationId: str
    applicationRunId: str
    packId: str
    codeEntryId: str
    codeEntryInstanceId: str


class CodeEntryContext:
    """Fresh invocation-scoped gateway supplied to Pack CodeEntry code.

    Every authority-bearing facade checks this Context's lifetime. Retaining a
    facade beyond callback completion therefore does not retain authority.
    """

    def __init__(self, *, identity: CodeEntryIdentity, packRoot: Path, io: ManagedIo, capabilities: CapabilityRegistry,
                 llmProviders: LlmProviderRegistry, llmPipeline: StreamingLlmPipeline, registrationScope: RegistrationScope,
                 config: dict[str, object], capabilityInvoker: Callable[[str, object | None], object]) -> None:
        self.identity = identity
        self.packRoot = packRoot
        self.config = deepcopy(config)
        self._valid = True
        self.io = _IoFacade(io=io, requireValid=self.requireValid)
        self.capabilities = _CapabilityFacade(
            ownerId=identity.codeEntryInstanceId,
            registry=capabilities,
            scope=registrationScope,
            invoker=capabilityInvoker,
            requireValid=self.requireValid,
        )
        self.llm = _LlmFacade(
            ownerId=identity.codeEntryInstanceId,
            registry=llmProviders,
            scope=registrationScope,
            pipeline=llmPipeline,
            requireValid=self.requireValid,
        )

    def requireValid(self) -> None:
        if not self._valid:
            raise RuntimeError("CodeEntryContext is no longer valid.")

    def invalidate(self) -> None:
        self._valid = False
