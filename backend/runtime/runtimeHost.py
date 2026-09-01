# file: backend/runtime/runtimeHost.py ; version: 4
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock

from backend.application.runtime import Application, ApplicationRun
from backend.capabilities.runtime import CapabilityRegistry
from backend.context.codeEntryContext import CodeEntryContext, CodeEntryIdentity
from backend.io.managedIo import ManagedIo
from backend.llm.streamingRuntime import LlmProviderRegistry, StreamingLlmPipeline
from backend.orchestration.runtime import Job, OrchestrationUnit, OrchestrationUnitOutcome
from backend.registration import RegistrationScope

__all__ = ["RuntimeHost"]


class RuntimeHost:
    """Running host boundary coordinating controlled surfaces for one proving-ground ApplicationRun.

    All capability execution enters one re-entrant ApplicationRun lane. Nested
    capability calls remain legal, while unrelated callers cannot execute the
    same ApplicationRun concurrently merely because Python threads are available.

    Bootstrap configuration is detached at construction and exposed only as
    snapshots. Caller-owned mutable dictionaries therefore cannot silently
    change later Pack invocations after the ApplicationRun has started.
    """

    def __init__(self, *, application: Application | None = None, config: dict[str, object] | None = None) -> None:
        self.applicationRun = ApplicationRun(application=application or Application.new())
        self.io = ManagedIo()
        self.capabilities = CapabilityRegistry()
        self.llmProviders = LlmProviderRegistry()
        self.llmPipeline = StreamingLlmPipeline(providers=self.llmProviders)
        self._config = {} if config is None else deepcopy(config)
        self._codeEntries: dict[str, tuple[CodeEntryIdentity, Path]] = {}
        self._lane = RLock()

    @property
    def config(self) -> dict[str, object]:
        """Returns a detached snapshot of the runtime bootstrap configuration."""
        return deepcopy(self._config)

    def start(self) -> None:
        with self._lane:
            self.applicationRun.start()

    def stop(self) -> None:
        with self._lane:
            if not self.applicationRun.active:
                return
            self.applicationRun.stop()

    def requireActive(self) -> None:
        if not self.applicationRun.active:
            raise RuntimeError("ApplicationRun is not active.")

    def createContext(self, *, identity: CodeEntryIdentity, packRoot: Path, registrationScope: RegistrationScope) -> CodeEntryContext:
        self.requireActive()
        return CodeEntryContext(
            identity=identity,
            packRoot=packRoot,
            io=self.io,
            capabilities=self.capabilities,
            llmProviders=self.llmProviders,
            llmPipeline=self.llmPipeline,
            registrationScope=registrationScope,
            config=self._config,
            capabilityInvoker=lambda capabilityId, payload=None: self.invokeCapability(capabilityId, payload),
        )

    def registerCodeEntry(self, identity: CodeEntryIdentity, packRoot: Path) -> None:
        self.requireActive()
        if identity.codeEntryInstanceId in self._codeEntries:
            raise RuntimeError(f"CodeEntry instance is already active: {identity.codeEntryInstanceId}.")
        self._codeEntries[identity.codeEntryInstanceId] = (identity, packRoot.resolve())

    def unregisterCodeEntry(self, codeEntryInstanceId: str) -> None:
        self._codeEntries.pop(codeEntryInstanceId, None)

    def invokeCapability(self, capabilityId: str, payload: object | None = None) -> object:
        with self._lane:
            self.requireActive()
            registration = self.capabilities.resolve(capabilityId)
            try:
                identity, packRoot = self._codeEntries[registration.ownerId]
            except KeyError as err:
                raise RuntimeError(f"Capability owner is not an active CodeEntry: {registration.ownerId}.") from err
            scope = RegistrationScope()
            context = self.createContext(identity=identity, packRoot=packRoot, registrationScope=scope)
            try:
                return self.capabilities.invokeResolved(registration, context=context, payload=payload)
            finally:
                context.invalidate()
                scope.withdraw()

    def runJob(self, capabilityId: str, payload: object | None = None) -> Job:
        """Runs one synchronous Job/OrchestrationUnit on the ApplicationRun lane."""
        with self._lane:
            self.requireActive()
            job = Job.new()
            unit = OrchestrationUnit.new()
            job.start()
            try:
                result = self.invokeCapability(capabilityId, payload)
            except Exception as err:
                unit.finish(OrchestrationUnitOutcome.FAILED)
                job.fail(err)
            else:
                unit.finish(OrchestrationUnitOutcome.COMPLETED)
                job.succeed(result)
            return job
