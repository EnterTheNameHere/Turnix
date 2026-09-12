# file: backend/context/codeEntryContext.py ; version: 22
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from backend.core.immutableValue import ImmutableValueFreezer
from backend.llm.errors import LlmProviderProtocolError
from backend.llm.llmTypes import LlmExecutionProfile, LlmQuery
from backend.process.context import ProcessFacade
from backend.process.runtime import ProcessRunner, ProcessToolRegistry
from backend.values.committed import ValueState
from backend.workspace.context import WorkspaceFacade
from backend.workspace.runtime import EphemeralWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from backend.capabilities.runtime import CapabilityHandler, CapabilityRegistry
    from backend.core.immutableValue import ImmutableValue
    from backend.io.managedIo import ManagedIo, ManagedIoTransaction
    from backend.llm.llmTypes import LlmStreamEvent, LlmStreamProvider
    from backend.llm.streamingRuntime import (
        LlmProcessingPipeline,
        LlmProcessingPreview,
        LlmProcessingResult,
        LlmProviderRegistry,
        StreamingLlmResult,
    )
    from backend.registration import RegistrationScope
    from backend.values.committed import CommittedValueLayer, CommittedValueTransaction

__all__ = ["CodeEntryContext", "CodeEntryIdentity"]


class _IoFacade:
    """Invocation-bound facade over Actant-mediated filesystem I/O."""

    def __init__(self, *, io: ManagedIo | ManagedIoTransaction, requireValid: Callable[[], None]) -> None:
        """Binds one managed-I/O view to the owning invocation lifetime."""
        self._io = io
        self._requireValid = requireValid

    def observeFile(self, path, *, contentHash: bool = False) -> dict[str, object]:
        """Returns JSON-compatible Actant source-observation evidence."""
        self._requireValid()
        return self._io.observeFile(path, contentHash=contentHash).snapshot()

    def readObservedText(self, path) -> dict[str, object]:
        """Returns exact UTF-8 text together with its source observation."""
        self._requireValid()
        value, observation = self._io.readObservedText(path)
        return {"value": value, "observation": observation.snapshot()}

    def readObservedLines(self, path) -> dict[str, object]:
        """Returns exact UTF-8 lines together with their source observation."""
        self._requireValid()
        value, observation = self._io.readObservedLines(path)
        return {"value": value, "observation": observation.snapshot()}

    def readObservedJson(self, path) -> dict[str, object]:
        """Returns JSON together with its Actant source observation."""
        self._requireValid()
        value, observation = self._io.readObservedJson(path)
        return {"value": value, "observation": observation.snapshot()}

    def readText(self, path):
        """Reads UTF-8 text through the invocation's managed-I/O view."""
        self._requireValid()
        return self._io.readText(path)

    def readJson(self, path):
        """Reads JSON through the invocation's managed-I/O view."""
        self._requireValid()
        return self._io.readJson(path)

    def readLines(self, path):
        """Reads UTF-8 lines through the invocation's managed-I/O view."""
        self._requireValid()
        return self._io.readLines(path)

    def writeTextAtomic(self, path, text: str) -> None:
        """Requests an atomic text write through the managed-I/O view."""
        self._requireValid()
        self._io.writeTextAtomic(path, text)

    def writeJsonAtomic(self, path, value: object) -> None:
        """Requests an atomic JSON write through the managed-I/O view."""
        self._requireValid()
        self._io.writeJsonAtomic(path, value)


class _MemoryTransactionFacade:
    """Invocation-bound access to one authoritative Value System transaction."""

    __slots__ = ("_producer", "_register", "_transaction", "_requireValid")

    def __init__(self, *, transaction: CommittedValueTransaction, requireValid: Callable[[], None], register: Callable[["_MemoryTransactionFacade"], None], producer: dict[str, object]) -> None:
        """Binds one speculative transaction to invocation and producer identity."""
        self._transaction = transaction
        self._requireValid = requireValid
        self._register = register
        self._producer = deepcopy(producer)

    def load(self, address: str) -> object:
        """Loads the value currently visible at an address."""
        self._requireValid()
        return self._transaction.load(address)

    def state(self, address: str) -> ValueState:
        """Returns the authority state currently visible at an address."""
        self._requireValid()
        return self._transaction.state(address)

    def metadata(self, address: str) -> dict[str, object] | None:
        """Returns generic Actant metadata visible through this transaction."""
        self._requireValid()
        return self._transaction.metadata(address)

    def describe(self, address: str) -> dict[str, object]:
        """Returns generic speculative revision/state/metadata evidence."""
        self._requireValid()
        return self._transaction.describe(address)

    def dependency(self, address: str) -> dict[str, object]:
        """Returns commit-stable identity for a derivation input."""
        self._requireValid()
        return self._transaction.dependency(address)

    def isCurrent(self, address: str, *, state: str, validity: dict[str, object]) -> bool:
        """Returns whether visible authority state matches producer and validity."""
        self._requireValid()
        try:
            expectedState = ValueState(state)
        except (TypeError, ValueError) as err:
            raise ValueError(f"Unsupported Value authority state {state!r}.") from err
        if self._transaction.state(address) is not expectedState:
            return False
        metadata = self._transaction.metadata(address)
        return isinstance(metadata, dict) and metadata.get("formatId") == "actant.derived-value-metadata@1" and metadata.get("producer") == self._producer and metadata.get("validity") == validity

    def isReusable(self, address: str, *, validity: dict[str, object]) -> bool:
        """Returns whether PRESENT state matches producer and validity."""
        return self.isCurrent(address, state="present", validity=validity)

    def set(self, address: str, value: object, *, validity: dict[str, object] | None = None, provenance: dict[str, object] | None = None) -> None:
        """Stages a value with producer and derivation metadata."""
        self._requireValid()
        self._transaction.set(address, value, metadata=self._metadata(validity=validity, provenance=provenance))

    def setAbsent(self, address: str, *, validity: dict[str, object] | None = None, provenance: dict[str, object] | None = None) -> None:
        """Stages authoritative absence with derivation metadata."""
        self._requireValid()
        self._transaction.setAbsent(address, metadata=self._metadata(validity=validity, provenance=provenance))

    def invalidate(self, address: str, *, validity: dict[str, object] | None = None, provenance: dict[str, object] | None = None) -> None:
        """Stages invalidation with derivation metadata."""
        self._requireValid()
        self._transaction.invalidate(address, metadata=self._metadata(validity=validity, provenance=provenance))

    def _metadata(self, *, validity: dict[str, object] | None, provenance: dict[str, object] | None) -> dict[str, object]:
        """Builds detached generic metadata for one staged transition."""
        if validity is not None and not isinstance(validity, dict):
            raise TypeError("Value validity metadata must be an object or null.")
        if provenance is not None and not isinstance(provenance, dict):
            raise TypeError("Value provenance metadata must be an object or null.")
        return {"formatId": "actant.derived-value-metadata@1", "producer": deepcopy(self._producer), "validity": {} if validity is None else deepcopy(validity), "provenance": {} if provenance is None else deepcopy(provenance)}

    def openTransaction(self) -> "_MemoryTransactionFacade":
        """Opens a nested invocation-owned speculative transaction."""
        self._requireValid()
        facade = _MemoryTransactionFacade(transaction=self._transaction.openTransaction(), requireValid=self._requireValid, register=self._register, producer=self._producer)
        self._register(facade)
        return facade

    def commit(self) -> None:
        """Commits this transaction into its parent authority boundary."""
        self._requireValid()
        self._transaction.commit()

    def abort(self) -> None:
        """Aborts this transaction without publishing staged transitions."""
        self._requireValid()
        self._transaction.abort()


class _MemoryFacade:
    """Invocation-scoped gateway to Application authoritative memory."""

    __slots__ = ("_openedTransactions", "_producer", "_state", "_requireValid")

    def __init__(self, *, state: CommittedValueLayer | CommittedValueTransaction, requireValid: Callable[[], None], producer: dict[str, object]) -> None:
        """Binds one visible Value System view to an invocation and producer."""
        self._state = state
        self._requireValid = requireValid
        self._producer = deepcopy(producer)
        self._openedTransactions: list[_MemoryTransactionFacade] = []

    def load(self, address: str) -> object:
        """Loads the value currently visible at an address."""
        self._requireValid()
        return self._state.load(address)

    def state(self, address: str) -> ValueState:
        """Returns the authority state currently visible at an address."""
        self._requireValid()
        return self._state.state(address)

    def revisionId(self, address: str) -> int:
        """Returns the visible committed revision identity for an address."""
        self._requireValid()
        return self._state.revisionId(address)

    def metadata(self, address: str) -> dict[str, object] | None:
        """Returns generic Actant metadata for the visible revision."""
        self._requireValid()
        return self._state.metadata(address)

    def describe(self, address: str) -> dict[str, object]:
        """Returns generic revision/state/metadata evidence."""
        self._requireValid()
        return self._state.describe(address)

    def dependency(self, address: str) -> dict[str, object]:
        """Returns commit-stable identity for a derivation input."""
        self._requireValid()
        return self._state.dependency(address)

    def isCurrent(self, address: str, *, state: str, validity: dict[str, object]) -> bool:
        """Returns whether visible authority state matches producer and validity."""
        self._requireValid()
        try:
            expectedState = ValueState(state)
        except (TypeError, ValueError) as err:
            raise ValueError(f"Unsupported Value authority state {state!r}.") from err
        if self._state.state(address) is not expectedState:
            return False
        metadata = self._state.metadata(address)
        return isinstance(metadata, dict) and metadata.get("formatId") == "actant.derived-value-metadata@1" and metadata.get("producer") == self._producer and metadata.get("validity") == validity

    def isReusable(self, address: str, *, validity: dict[str, object]) -> bool:
        """Returns whether PRESENT state matches producer and validity."""
        return self.isCurrent(address, state="present", validity=validity)

    def openTransaction(self) -> _MemoryTransactionFacade:
        """Opens a nested speculative transaction owned by this invocation."""
        self._requireValid()
        facade = _MemoryTransactionFacade(transaction=self._state.openTransaction(), requireValid=self._requireValid, register=self._openedTransactions.append, producer=self._producer)
        self._openedTransactions.append(facade)
        return facade

    def close(self) -> None:
        """Best-effort aborts unresolved transactions owned by this invocation."""
        for transaction in reversed(self._openedTransactions):
            try:
                transaction._transaction.abort()
            except RuntimeError:
                pass
        self._openedTransactions.clear()


class _CapabilityFacade:
    """Invocation-scoped gateway to capability registration and invocation."""

    def __init__(self, *, ownerId: str, registry: CapabilityRegistry, scope: RegistrationScope, invoker: Callable[[str, object | None], object], requireValid: Callable[[], None], allowRegistration: bool) -> None:
        """Binds capability authority to one CodeEntry invocation and owner."""
        self._ownerId = ownerId
        self._registry = registry
        self._scope = scope
        self._invoker = invoker
        self._requireValid = requireValid
        self._allowRegistration = allowRegistration

    def register(self, capabilityId: str, handler: CapabilityHandler) -> None:
        """Registers a capability when this invocation permits registration."""
        self._requireValid()
        if not self._allowRegistration:
            raise RuntimeError("Capability registration is not available in this invocation Context.")
        self._registry.register(self._scope, ownerId=self._ownerId, capabilityId=capabilityId, handler=handler)

    def call(self, capabilityId: str, payload: object | None = None) -> object:
        """Invokes a capability through runtime mediation."""
        self._requireValid()
        return self._invoker(capabilityId, payload)


class _LlmFacade:
    """Invocation-scoped gateway to registered LLM providers and processing."""

    def __init__(self, *, ownerId: str, registry: LlmProviderRegistry, scope: RegistrationScope, pipeline: LlmProcessingPipeline, memory: CommittedValueLayer | CommittedValueTransaction, requireValid: Callable[[], None], allowRegistration: bool) -> None:
        """Binds LLM authority to one CodeEntry invocation and memory view."""
        self._ownerId = ownerId
        self._registry = registry
        self._scope = scope
        self._pipeline = pipeline
        self._memory = memory
        self._requireValid = requireValid
        self._allowRegistration = allowRegistration

    def registerProvider(self, name: str, provider: LlmStreamProvider) -> None:
        """Registers an LLM provider when this invocation permits registration."""
        self._requireValid()
        if not self._allowRegistration:
            raise RuntimeError("LLM provider registration is not available in this invocation Context.")
        self._registry.register(self._scope, ownerId=self._ownerId, name=name, provider=provider)

    def estimateInputTokens(self, *, providerName: str, query: LlmQuery, model: str | None = None, providerOptions: Mapping[str, ImmutableValue] | None = None) -> int:
        """Returns provider/model-aware input-token usage without inference."""
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
            raise LlmProviderProtocolError(f"LLM provider {providerName!r} token estimator returned an invalid value: {result!r}.")
        return result

    def run(self, *, providerName: str, query: LlmQuery, model: str | None = None, providerOptions: Mapping[str, ImmutableValue] | None = None, streamObserver: Callable[[LlmStreamEvent], None] | None = None) -> StreamingLlmResult:
        """Runs one provider-neutral query through the registered provider."""
        self._requireValid()
        return self._pipeline.run(providerName=providerName, query=query, model=model, providerOptions=providerOptions, streamObserver=streamObserver)

    def prepareProcessing(self, *, memoryKey: str, inputValue: object, buildQueryItemsCapabilityId: str, buildQueryCapabilityId: str, providerName: str, model: str | None = None, providerOptions: Mapping[str, ImmutableValue] | None = None, filterQueryItemsCapabilityId: str | None = None) -> LlmProcessingPreview:
        """Prepares the exact model-facing query without provider inference."""
        self._requireValid()
        return self._pipeline.prepareProcessing(memoryKey=memoryKey, inputValue=inputValue, buildQueryItemsCapabilityId=buildQueryItemsCapabilityId, buildQueryCapabilityId=buildQueryCapabilityId, filterQueryItemsCapabilityId=filterQueryItemsCapabilityId, providerName=providerName, model=model, providerOptions=providerOptions, memoryView=self._memory)

    def runProcessing(self, *, memoryKey: str, inputValue: object, buildQueryItemsCapabilityId: str, buildQueryCapabilityId: str, providerName: str, model: str | None = None, providerOptions: Mapping[str, ImmutableValue] | None = None, filterQueryItemsCapabilityId: str | None = None, completionCapabilityId: str | None = None, completionInput: object | None = None, streamObserver: Callable[[LlmStreamEvent], None] | None = None) -> LlmProcessingResult:
        """Runs one transactional LLM ProcessingRun against this memory view."""
        self._requireValid()
        return self._pipeline.runProcessing(memoryKey=memoryKey, inputValue=inputValue, buildQueryItemsCapabilityId=buildQueryItemsCapabilityId, buildQueryCapabilityId=buildQueryCapabilityId, filterQueryItemsCapabilityId=filterQueryItemsCapabilityId, completionCapabilityId=completionCapabilityId, completionInput=completionInput, providerName=providerName, model=model, providerOptions=providerOptions, streamObserver=streamObserver, memoryView=self._memory)


@dataclass(frozen=True, slots=True)
class CodeEntryIdentity:
    """Runtime and implementation identity of one loaded CodeEntry instance."""

    applicationId: str
    applicationRunId: str
    packId: str
    packVersion: str
    codeEntryId: str
    codeEntryInstanceId: str
    sourceSha256: str
    implementationFormat: str
    implementationId: str

    def producerSnapshot(self) -> dict[str, object]:
        """Returns stable producer identity suitable for persisted Value metadata."""
        return {"packId": self.packId, "packVersion": self.packVersion, "codeEntryId": self.codeEntryId, "sourceSha256": self.sourceSha256, "implementationFormat": self.implementationFormat, "implementationId": self.implementationId}


class CodeEntryContext:
    """Fresh invocation-scoped gateway supplied to Pack CodeEntry code."""

    def __init__(self, *, identity: CodeEntryIdentity, packRoot: Path, io: ManagedIo | ManagedIoTransaction, capabilities: CapabilityRegistry, llmProviders: LlmProviderRegistry, llmPipeline: LlmProcessingPipeline, memory: CommittedValueLayer | CommittedValueTransaction, registrationScope: RegistrationScope, config: dict[str, object], capabilityInvoker: Callable[[str, object | None], object], process: ProcessFacade | None = None, workspace: EphemeralWorkspace | None = None, allowRegistration: bool = False) -> None:
        """Creates one call-specific authority context from runtime-owned facilities.

        Production ApplicationRuntime construction supplies process authority and
        an invocation-owned ephemeral workspace explicitly. Low-level direct
        construction defaults to deny-all process authority and still receives a
        private workspace so no ambient filesystem write authority is implied.
        """
        self.identity = identity
        self.packRoot = packRoot
        self.config = deepcopy(config)
        self._valid = True
        self._workspace = workspace or EphemeralWorkspace()
        self.io = _IoFacade(io=io, requireValid=self.requireValid)
        self.workspace = WorkspaceFacade(workspace=self._workspace, requireValid=self.requireValid)
        self.process = process or ProcessFacade(runner=ProcessRunner(ProcessToolRegistry()), requireValid=self.requireValid)
        self.memory = _MemoryFacade(state=memory, requireValid=self.requireValid, producer=identity.producerSnapshot())
        self.capabilities = _CapabilityFacade(ownerId=identity.codeEntryInstanceId, registry=capabilities, scope=registrationScope, invoker=capabilityInvoker, requireValid=self.requireValid, allowRegistration=allowRegistration)
        self.llm = _LlmFacade(ownerId=identity.codeEntryInstanceId, registry=llmProviders, scope=registrationScope, pipeline=llmPipeline, memory=memory, requireValid=self.requireValid, allowRegistration=allowRegistration)

    def requireValid(self) -> None:
        """Rejects use after Actant has ended this invocation turn."""
        if not self._valid:
            raise RuntimeError("CodeEntryContext is no longer valid.")

    def invalidate(self) -> None:
        """Ends invocation authority and releases invocation-owned resources."""
        if not self._valid:
            return
        self.memory.close()
        self._workspace.close()
        self._valid = False
