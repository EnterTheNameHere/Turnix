from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock

from backend.application.runtime import Application, ApplicationRun
from backend.capabilities.runtime import CapabilityRegistry
from backend.context.codeEntryContext import CodeEntryContext, CodeEntryIdentity
from backend.io.managedIo import ManagedIo
from backend.llm.streamingRuntime import LlmProviderRegistry, LlmProcessingPipeline
from backend.orchestration.runtime import Job, OrchestrationUnit, OrchestrationUnitOutcome
from backend.registration import RegistrationScope
from backend.tracing import TraceSinkDestination, Tracer

__all__ = ["RuntimeHost"]


class RuntimeHost:
    """Running host boundary coordinating controlled surfaces for one ApplicationRun."""

    def __init__(
        self,
        *,
        application: Application | None = None,
        config: dict[str, object] | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self.applicationRun = ApplicationRun(application=application or Application.new())
        self.io = ManagedIo()
        self.capabilities = CapabilityRegistry()
        self.llmProviders = LlmProviderRegistry()
        self._config = {} if config is None else deepcopy(config)
        self._codeEntries: dict[str, tuple[CodeEntryIdentity, Path]] = {}
        self._lane = RLock()
        self._ownsTracer = tracer is None
        self.tracer = tracer or Tracer(origin="actant.runtime", destinations=(TraceSinkDestination(),))
        self.llmPipeline = LlmProcessingPipeline(
            providers=self.llmProviders,
            state=self.applicationRun.committedState,
            capabilityInvoker=lambda capabilityId, payload=None: self.invokeCapability(capabilityId, payload),
            trace=lambda reason, attributes: self.trace(reason, attributes=attributes),
        )

    @property
    def config(self) -> dict[str, object]:
        return deepcopy(self._config)

    def trace(
        self,
        reason: str,
        *,
        message: str = "",
        attributes: dict[str, object] | None = None,
        level: str = "info",
    ) -> None:
        self.tracer.emitEvent(
            domain="runtime",
            level=level,
            message=message,
            label=reason,
            attributes={} if attributes is None else attributes,
        )

    def start(self) -> None:
        with self._lane:
            self.applicationRun.start()
            self.trace(
                "application-run-started",
                attributes={
                    "applicationId": self.applicationRun.application.applicationId,
                    "applicationRunId": self.applicationRun.applicationRunId,
                },
            )

    def stop(self) -> None:
        with self._lane:
            if not self.applicationRun.active:
                return
            self.trace(
                "application-run-stopped",
                attributes={
                    "applicationId": self.applicationRun.application.applicationId,
                    "applicationRunId": self.applicationRun.applicationRunId,
                },
            )
            self.applicationRun.stop()
            if self._ownsTracer:
                self.tracer.close()

    def requireActive(self) -> None:
        if not self.applicationRun.active:
            raise RuntimeError("ApplicationRun is not active.")

    def createContext(
        self,
        *,
        identity: CodeEntryIdentity,
        packRoot: Path,
        registrationScope: RegistrationScope,
        allowRegistration: bool = False,
    ) -> CodeEntryContext:
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
            allowRegistration=allowRegistration,
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
            self.trace(
                "capability-invocation-started",
                attributes={
                    "capabilityId": capabilityId,
                    "ownerId": registration.ownerId,
                    "codeEntryInstanceId": identity.codeEntryInstanceId,
                },
            )
            scope = RegistrationScope()
            context = self.createContext(identity=identity, packRoot=packRoot, registrationScope=scope)
            try:
                result = self.capabilities.invokeResolved(registration, context=context, payload=payload)
            except Exception as err:
                self.trace(
                    "capability-invocation-failed",
                    message=str(err),
                    attributes={"capabilityId": capabilityId, "ownerId": registration.ownerId},
                    level="error",
                )
                raise
            else:
                self.trace(
                    "capability-invocation-completed",
                    attributes={"capabilityId": capabilityId, "ownerId": registration.ownerId},
                )
                return result
            finally:
                context.invalidate()
                scope.withdraw()

    def runJob(self, capabilityId: str, payload: object | None = None) -> Job:
        with self._lane:
            self.requireActive()
            job = Job.new()
            unit = OrchestrationUnit.new()
            job.start()
            self.trace(
                "job-started",
                attributes={
                    "jobId": job.jobId,
                    "orchestrationUnitId": unit.orchestrationUnitId,
                    "capabilityId": capabilityId,
                },
            )
            try:
                result = self.invokeCapability(capabilityId, payload)
            except Exception as err:
                unit.finish(OrchestrationUnitOutcome.FAILED)
                job.fail(err)
                self.trace(
                    "job-failed",
                    message=str(err),
                    attributes={
                        "jobId": job.jobId,
                        "orchestrationUnitId": unit.orchestrationUnitId,
                        "capabilityId": capabilityId,
                    },
                    level="error",
                )
            else:
                unit.finish(OrchestrationUnitOutcome.COMPLETED)
                job.succeed(result)
                self.trace(
                    "job-completed",
                    attributes={
                        "jobId": job.jobId,
                        "orchestrationUnitId": unit.orchestrationUnitId,
                        "capabilityId": capabilityId,
                    },
                )
            return job
