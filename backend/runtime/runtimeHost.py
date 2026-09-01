# file: backend/runtime/runtimeHost.py ; version: 2
from __future__ import annotations

from pathlib import Path

from backend.application.runtime import Application, ApplicationRun
from backend.capabilities.runtime import CapabilityRegistry
from backend.context.codeEntryContext import CodeEntryContext, CodeEntryIdentity
from backend.io.managedIo import ManagedIo
from backend.llm.streamingRuntime import LlmProviderRegistry, StreamingLlmPipeline
from backend.orchestration.runtime import Job, OrchestrationUnit, OrchestrationUnitOutcome
from backend.registration import RegistrationScope

__all__ = ["RuntimeHost"]


class RuntimeHost:
    """Running host boundary coordinating controlled surfaces for one proving-ground ApplicationRun."""

    def __init__(self, *, application: Application | None = None, config: dict[str, object] | None = None) -> None:
        self.applicationRun = ApplicationRun(application=application or Application.new())
        self.io = ManagedIo()
        self.capabilities = CapabilityRegistry()
        self.llmProviders = LlmProviderRegistry()
        self.llmPipeline = StreamingLlmPipeline(providers=self.llmProviders)
        self.config = {} if config is None else dict(config)
        self._codeEntries: dict[str, tuple[CodeEntryIdentity, Path]] = {}

    def start(self) -> None:
        self.applicationRun.start()

    def stop(self) -> None:
        self.applicationRun.stop()

    def createContext(self, *, identity: CodeEntryIdentity, packRoot: Path, registrationScope: RegistrationScope) -> CodeEntryContext:
        return CodeEntryContext(
            identity=identity,
            packRoot=packRoot,
            io=self.io,
            capabilities=self.capabilities,
            llmProviders=self.llmProviders,
            llmPipeline=self.llmPipeline,
            registrationScope=registrationScope,
            config=self.config,
            capabilityInvoker=lambda capabilityId, payload=None: self.invokeCapability(capabilityId, payload),
        )

    def registerCodeEntry(self, identity: CodeEntryIdentity, packRoot: Path) -> None:
        self._codeEntries[identity.codeEntryInstanceId] = (identity, packRoot)

    def invokeCapability(self, capabilityId: str, payload: object | None = None) -> object:
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
        """Runs one synchronous Job/OrchestrationUnit on the current single mutation lane."""
        job = Job.new()
        unit = OrchestrationUnit.new()
        job.start()
        try:
            result = self.invokeCapability(capabilityId, payload)
        except BaseException as err:
            unit.finish(OrchestrationUnitOutcome.FAILED)
            job.fail(err)
        else:
            unit.finish(OrchestrationUnitOutcome.COMPLETED)
            job.succeed(result)
        return job
