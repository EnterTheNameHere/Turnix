# file: backend/context/codeEntryContext.py ; version: 4
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from backend.core.immutableValue import ImmutableValueFreezer
from backend.llm.errors import LlmProviderProtocolError
from backend.llm.llmTypes import LlmExecutionProfile, LlmQuery

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from backend.capabilities.runtime import CapabilityHandler, CapabilityRegistry
    from backend.core.immutableValue import ImmutableValue
    from backend.io.managedIo import ManagedIo
    from backend.llm.llmTypes import LlmStreamEvent, LlmStreamProvider
    from backend.llm.streamingRuntime import LlmProcessingPipeline, LlmProcessingResult, LlmProviderRegistry, StreamingLlmResult
    from backend.registration import RegistrationScope
    from backend.values.committed import CommittedValueLayer, CommittedValueTransaction, ValueState

__all__ = ["CodeEntryContext", "CodeEntryIdentity"]


class _IoFacade:
    def __init__(self, *, io: ManagedIo, requireValid: Callable[[], None]) -> None:
        self._io = io
        self._requireValid = requireValid

    def observeFile(self, path, *, contentHash: bool = False) -> dict[str, object]:
        """Returns JSON-compatible Actant source-observation evidence."""
        self._requireValid()
        return self._io.observeFile(path, contentHash=contentHash).snapshot()

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


class _MemoryTransactionFacade:
    """Invocation-bound access to one authoritative Value System transaction.

    The underlying transaction belongs to Actant. This facade prevents Pack
    code from retaining usable transaction authority after its CodeEntryContext
    has been invalidated. Nested transactions inherit the same invocation
    lifetime.

    Packs define what makes their logical values authoritative. Actant owns
    stable addresses, authority state, revisions, conflict detection, and
    transactional publication. This division is design-significant and should
    be promoted into the Value System/CodeEntry design documents later.
    """

    __slots__ = ("_register", "_transaction", "_requireValid")

    def __init__(
        self,
        *,
        transaction: CommittedValueTransaction,
        requireValid: Callable[[], None],
        register: Callable[["_MemoryTransactionFacade"], None],
    ) -> None:
        self._transaction = transaction
        self._requireValid = requireValid
        self._register = register

    def load(self, address: str) -> object:
        self._requireValid()
        return self._transaction.load(address)

    def state(self, address: str) -> ValueState:
        self._requireValid()
        return self._transaction.state(address)

    def set(self, address: str, value: object) -> None:
        self._requireValid()
        self._transaction.set(address, value)

    def setAbsent(self, address: str) -> None:
        self._requireValid()
        self._transaction.setAbsent(address)

    def invalidate(self, address: str) -> None:
        self._requireValid()
        self._transaction.invalidate(address)

    def openTransaction(self) -> "_MemoryTransactionFacade":
        self._requireValid()
        facade = _MemoryTransactionFacade(
            transaction=self._transaction.openTransaction(),
            requireValid=self._requireValid,
            register=self._register,
        )
        self._register(facade)
        return facade

    def commit(self) -> None:
        self._requireValid()
        self._transaction.commit()

    def abort(self) -> None:
        self._requireValid()
        self._transaction.abort()


class _MemoryFacade:
    """Invocation-scoped gateway to Application authoritative memory.

    Reads observe committed state. Mutation is available only through
    openTransaction(), preserving Actant's transaction boundary and automatic
    revision advancement. state() distinguishes absent from invalidated without
    requiring Packs to encode authority flags inside their own payloads.
    """

    __slots__ = ("_openedTransactions", "_state", "_requireValid")

    def __init__(
        self,
        *,
        state: CommittedValueLayer | CommittedValueTransaction,
        requireValid: Callable[[], None],
    ) -> None:
        self._state = state
        self._requireValid = requireValid
        self._openedTransactions: list[_MemoryTransactionFacade] = []

    def load(self, address: str) -> object:
        self._requireValid()
        return self._state.load(address)

    def state(self, address: str) -> ValueState:
        self._requireValid()
        return self._state.state(address)

    def revisionId(self, address: str) -> int:
        self._requireValid()
        return self._state.revisionId(address)

    def openTransaction(self) -> _MemoryTransactionFacade:
        self._requireValid()
        facade = _MemoryTransactionFacade(
            transaction=self._state.openTransaction(),
            requireValid=self._requireValid,
            register=self._openedTransactions.append,
        )
        self._openedTransactions.append(facade)
        return facade


    def close(self) -> None:
        """Best-effort aborts unresolved transactions owned by this invocation.

        Context lifetime owns every transaction opened through ctx.memory.
        Resolved transactions reject abort(), which is harmless here; unresolved
        children are processed in reverse creation order so their parents can
        subsequently be resolved by enclosing runtime logic.
        """
        for transaction in reversed(self._openedTransactions):
            try:
                transaction._transaction.abort()
            except RuntimeError:
                pass
        self._openedTransactions.clear()


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
        memory: CommittedValueLayer | CommittedValueTransaction,
        requireValid: Callable[[], None],
        allowRegistration: bool,
    ) -> None:
        self._ownerId = ownerId
        self._registry = registry
        self._scope = scope
        self._pipeline = pipeline
        self._memory = memory
        self._requireValid = requireValid
        self._allowRegistration = allowRegistration

    def registerProvider(self, name: str, provider: LlmStreamProvider) -> None:
        self._requireValid()
        if not self._allowRegistration:
            raise RuntimeError("LLM provider registration is not available in this invocation Context.")
        self._registry.register(self._scope, ownerId=self._ownerId, name=name, provider=provider)

    def estimateInputTokens(
        self,
        *,
        providerName: str,
        query: LlmQuery,
        model: str | None = None,
        providerOptions: Mapping[str, ImmutableValue] | None = None,
    ) -> int:
        """Returns provider/model-aware input-token usage without starting inference."""
        self._requireValid()
        if not isinstance(query, LlmQuery):
            raise TypeError("query must be an LlmQuery.")
        registration = self._registry.requireRegistration(providerName)
        options = ImmutableValueFreezer().freezeMapping(providerOptions, "providerOptions")
        profile = registration.value.getExecutionProfile(model=model, providerOptions=options)
        if not isinstance(profile, LlmExecutionProfile):
            raise LlmProviderProtocolError("Provider getExecutionProfile() returned an invalid value.")
        estimator = profile.tokenEstimator
        if estimator is None:
            raise RuntimeError(f"LLM provider {providerName!r} does not expose an input-token estimator.")
        result = estimator.estimateInputTokens(query)
        if type(result) is not int or result < 0:
            raise LlmProviderProtocolError(
                f"LLM provider {providerName!r} token estimator returned an invalid value: {result!r}.",
            )
        return result

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
        finalizeCapabilityId: str | None = None,
        finalizeInput: object | None = None,
        streamObserver: Callable[[LlmStreamEvent], None] | None = None,
    ) -> LlmProcessingResult:
        self._requireValid()
        return self._pipeline.runProcessing(
            memoryKey=memoryKey,
            inputValue=inputValue,
            buildQueryItemsCapabilityId=buildQueryItemsCapabilityId,
            buildQueryCapabilityId=buildQueryCapabilityId,
            filterQueryItemsCapabilityId=filterQueryItemsCapabilityId,
            finalizeCapabilityId=finalizeCapabilityId,
            finalizeInput=finalizeInput,
            providerName=providerName,
            model=model,
            providerOptions=providerOptions,
            streamObserver=streamObserver,
            memoryView=self._memory,
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
        memory: CommittedValueLayer | CommittedValueTransaction,
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
        self.memory = _MemoryFacade(state=memory, requireValid=self.requireValid)
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
            memory=memory,
            requireValid=self.requireValid,
            allowRegistration=allowRegistration,
        )

    def requireValid(self) -> None:
        if not self._valid:
            raise RuntimeError("CodeEntryContext is no longer valid.")

    def invalidate(self) -> None:
        if not self._valid:
            return
        self.memory.close()
        self._valid = False
