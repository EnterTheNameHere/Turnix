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
    from backend.llm.streamingRuntime import LlmProcessingPipeline, LlmProcessingResult, LlmProviderRegistry, StreamingLlmResult
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
    def __init__(
        self,
        *,
        ownerId: str,
        registry: CapabilityRegistry,
        scope: RegistrationScope,
        invoker: Callable[[str, object | None], object],
        requireValid: Callable[[], None],
        allowRegistration: bool,
    ) -> None:
        self._ownerId = ownerId
        self._registry = registry
        self._scope = scope
        self._invoker = invoker
        self._requireValid = requireValid
        self._allowRegistration = allowRegistration

    def register(self, capabilityId: str, handler: CapabilityHandler) -> None:
        self._requireValid()
        if not self._allowRegistration:
            raise RuntimeError("Capability registration is not available in this invocation Context.")
        self._registry.register(self._scope, ownerId=self._ownerId, capabilityId=capabilityId, handler=handler)

    def call(self, capabilityId: str, payload: object | None = None) -> object:
        self._requireValid()
        return self._invoker(capabilityId, payload)


class _LlmFacade:
    def __init__(
        self,
        *,
        ownerId: str,
        registry: LlmProviderRegistry,
        scope: RegistrationScope,
        pipeline: LlmProcessingPipeline,
        requireValid: Callable[[], None],
        allowRegistration: bool,
    ) -> None:
        self._ownerId = ownerId
        self._registry = registry
        self._scope = scope
        self._pipeline = pipeline
        self._requireValid = requireValid
        self._allowRegistration = allowRegistration

    def registerProvider(self, name: str, provider: LlmStreamProvider) -> None:
        self._requireValid()
        if not self._allowRegistration:
            raise RuntimeError("LLM provider registration is not available in this invocation Context.")
        self._registry.register(self._scope, ownerId=self._ownerId, name=name, provider=provider)

    def run(
        self,
        *,
        providerName: str,
        query: LlmQuery,
        model: str | None = None,
        providerOptions: Mapping[str, ImmutableValue] | None = None,
        streamObserver: Callable[[LlmStreamEvent], None] | None = None,
    ) -> StreamingLlmResult:
        self._requireValid()
        return self._pipeline.run(
            providerName=providerName,
            query=query,
            model=model,
            providerOptions=providerOptions,
            streamObserver=streamObserver,
        )

    def runProcessing(
        self,
        *,
        memoryKey: str,
        inputValue: object,
        buildQueryItemsCapabilityId: str,
        buildQueryCapabilityId: str,
        providerName: str,
        model: str | None = None,
        providerOptions: Mapping[str, ImmutableValue] | None = None,
        filterQueryItemsCapabilityId: str | None = None,
        streamObserver: Callable[[LlmStreamEvent], None] | None = None,
    ) -> LlmProcessingResult:
        self._requireValid()
        return self._pipeline.runProcessing(
            memoryKey=memoryKey,
            inputValue=inputValue,
            buildQueryItemsCapabilityId=buildQueryItemsCapabilityId,
            buildQueryCapabilityId=buildQueryCapabilityId,
            filterQueryItemsCapabilityId=filterQueryItemsCapabilityId,
            providerName=providerName,
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
    """Fresh invocation-scoped gateway supplied to Pack CodeEntry code."""

    def __init__(
        self,
        *,
        identity: CodeEntryIdentity,
        packRoot: Path,
        io: ManagedIo,
        capabilities: CapabilityRegistry,
        llmProviders: LlmProviderRegistry,
        llmPipeline: LlmProcessingPipeline,
        registrationScope: RegistrationScope,
        config: dict[str, object],
        capabilityInvoker: Callable[[str, object | None], object],
        allowRegistration: bool = False,
    ) -> None:
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
            allowRegistration=allowRegistration,
        )
        self.llm = _LlmFacade(
            ownerId=identity.codeEntryInstanceId,
            registry=llmProviders,
            scope=registrationScope,
            pipeline=llmPipeline,
            requireValid=self.requireValid,
            allowRegistration=allowRegistration,
        )

    def requireValid(self) -> None:
        if not self._valid:
            raise RuntimeError("CodeEntryContext is no longer valid.")

    def invalidate(self) -> None:
        self._valid = False
