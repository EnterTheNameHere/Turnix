# file: backend/application/applicationRuntime.py ; version: 9
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock

from backend.application.runtime import Application, ApplicationRun, ApplicationRunState
from backend.capabilities.runtime import CapabilityRegistry
from backend.context.codeEntryContext import CodeEntryContext, CodeEntryIdentity
from backend.io.managedIo import ManagedIo, ManagedIoTransaction
from backend.llm.streamingRuntime import LlmProviderRegistry, LlmProcessingPipeline
from backend.orchestration.runtime import Job, OrchestrationUnit, OrchestrationUnitOutcome
from backend.packs.runtime import PackLoader, PackResolver
from backend.registration import RegistrationScope
from backend.save import ApplicationStore, LoadedApplicationSave, SaveBundle
from backend.tracing import TraceSinkDestination, Tracer
from backend.values.committed import CommittedValueLayer, CommittedValueTransaction

__all__ = ["ApplicationRuntime"]


class ApplicationRuntime:
    """Live Actant execution environment for exactly one ApplicationRun.

    Tracing is evidence only. Trace publication or tracer-close failures are
    deliberately isolated here so loss of observability cannot alter runtime
    lifecycle, capability execution, Job outcome, or authoritative state.
    """

    def __init__(
        self,
        *,
        appPackId: str | None = None,
        application: Application | None = None,
        saveBundle: SaveBundle | None = None,
        applicationStore: ApplicationStore | None = None,
        packResolver: PackResolver,
        config: dict[str, object] | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        if application is not None and saveBundle is not None:
            raise ValueError("ApplicationRuntime accepts either application or saveBundle, not both.")
        if application is not None and appPackId is not None and application.appPackId != appPackId:
            raise ValueError("ApplicationRuntime appPackId does not match supplied Application.")
        if saveBundle is not None and appPackId is not None and saveBundle.appPackId != appPackId:
            raise ValueError("ApplicationRuntime appPackId does not match supplied SaveBundle.")

        if applicationStore is not None and not isinstance(applicationStore, ApplicationStore):
            raise TypeError("applicationStore must be an ApplicationStore.")
        if not isinstance(packResolver, PackResolver):
            raise TypeError("packResolver must be a PackResolver.")
        self._applicationStore = applicationStore
        self._saveBundle = saveBundle
        if saveBundle is None:
            if application is None:
                if type(appPackId) is not str or not appPackId:
                    raise ValueError("ApplicationRuntime requires appPackId when creating a new Application.")
                resolvedApplication = Application.new(appPackId=appPackId)
            else:
                resolvedApplication = application
        else:
            resolvedApplication = Application(
                appPackId=saveBundle.appPackId,
                applicationId=saveBundle.applicationId,
                committedState=saveBundle.restoreCommittedState(),
                saveBundleId=saveBundle.saveBundleId,
                durableGeneration=saveBundle.generation,
            )

        self.applicationRun = ApplicationRun(application=resolvedApplication)
        self.io = ManagedIo()
        self.capabilities = CapabilityRegistry()
        self.llmProviders = LlmProviderRegistry()
        self._config = {} if config is None else deepcopy(config)
        self._codeEntries: dict[str, tuple[CodeEntryIdentity, Path]] = {}
        self._lane = RLock()
        self._initializing = False
        self._ownsTracer = tracer is None
        self._tracerClosed = False
        self._closed = False
        self.tracer = tracer or Tracer(origin="actant.runtime", destinations=(TraceSinkDestination(),))
        self.llmPipeline = LlmProcessingPipeline(
            providers=self.llmProviders,
            state=self.applicationRun.application.committedState,
            capabilityInvoker=lambda capabilityId, payload=None, memoryView=None: self.invokeCapability(capabilityId, payload, memoryView=memoryView),
            trace=lambda reason, attributes: self.trace(reason, attributes=attributes),
        )
        self.packLoader = PackLoader(runtime=self, resolver=packResolver)

    @property
    def config(self) -> dict[str, object]:
        return deepcopy(self._config)

    @property
    def acceptedSaveBundle(self) -> SaveBundle | None:
        """Returns this Application's currently accepted immutable SaveBundle generation."""
        return self._saveBundle

    @classmethod
    def loadApplication(
        cls,
        *,
        applicationStore: ApplicationStore,
        appPackId: str,
        applicationId: str,
        packResolver: PackResolver,
        config: dict[str, object] | None = None,
        tracer: Tracer | None = None,
    ) -> tuple["ApplicationRuntime", LoadedApplicationSave]:
        """Creates a fresh ApplicationRuntime from one durable Application root snapshot.

        Loading restores only the SaveBundle's committed root. No transaction
        hierarchy exists in the new runtime. AppPack lifecycle hooks are a later
        layer above this storage operation.
        """
        if not isinstance(applicationStore, ApplicationStore):
            raise TypeError("applicationStore must be an ApplicationStore.")
        loaded = applicationStore.load(
            appPackId=appPackId,
            applicationId=applicationId,
        )
        runtime = cls(
            saveBundle=loaded.bundle,
            applicationStore=applicationStore,
            packResolver=packResolver,
            config=config,
            tracer=tracer,
        )
        runtime.applicationRun.application.durableGeneration = loaded.durableGeneration
        return runtime, loaded

    def _nextSaveBundleCandidate(self) -> SaveBundle:
        """Captures root state without advancing accepted persistence identity."""
        if self._saveBundle is None:
            return SaveBundle.create(
                appPackId=self.applicationRun.application.appPackId,
                applicationId=self.applicationRun.application.applicationId,
                committedState=self.applicationRun.application.committedState,
            )
        if (
            self._saveBundle.appPackId != self.applicationRun.application.appPackId
            or self._saveBundle.applicationId != self.applicationRun.application.applicationId
        ):
            raise RuntimeError("Bound SaveBundle Application identity no longer matches ApplicationRun.")
        application = self.applicationRun.application
        durableGeneration = application.durableGeneration
        if durableGeneration is None:
            durableGeneration = self._saveBundle.generation
        targetGeneration = durableGeneration + 1
        if targetGeneration == self._saveBundle.generation + 1:
            return self._saveBundle.nextGeneration(
                committedState=application.committedState,
            )
        return self._saveBundle.advanceToGeneration(
            generation=targetGeneration,
            committedState=application.committedState,
        )

    def _acceptSaveBundle(self, bundle: SaveBundle) -> None:
        self._saveBundle = bundle
        application = self.applicationRun.application
        application.saveBundleId = bundle.saveBundleId
        application.durableGeneration = bundle.generation

    def saveApplication(self, applicationStore: ApplicationStore | None = None) -> SaveBundle:
        """Persists exactly one snapshot of the current authoritative root.

        Filesystem publication is attempted before the runtime accepts the new
        SaveBundle generation. A failed publication therefore leaves the
        runtime's accepted durable generation unchanged and retryable.
        """
        with self._lane:
            store = applicationStore or self._applicationStore
            if store is None:
                raise RuntimeError("ApplicationRuntime has no ApplicationStore bound for persistence.")
            if not isinstance(store, ApplicationStore):
                raise TypeError("applicationStore must be an ApplicationStore.")

            candidate = self._nextSaveBundleCandidate()
            if self._saveBundle is None:
                store.createApplication(candidate)
            else:
                store.publish(candidate)
            self._applicationStore = store
            self._acceptSaveBundle(candidate)
            return candidate

    def captureSaveBundle(self) -> SaveBundle:
        """Captures the next in-memory SaveBundle generation for this Application.

        The returned bundle protects committed state at the instant of capture.
        This method does not claim filesystem or persistent-I/O publication;
        storage authority remains a separate boundary.

        When this host was loaded from a SaveBundle, capture preserves that
        saveBundleId and advances its generation. For a new Application, the
        first capture establishes generation 1 of a new SaveBundle identity.
        """
        with self._lane:
            bundle = self._nextSaveBundleCandidate()
            self._acceptSaveBundle(bundle)
            return bundle

    def trace(
        self,
        reason: str,
        *,
        message: str = "",
        attributes: dict[str, object] | None = None,
        level: str = "info",
    ) -> bool:
        """Attempts to emit runtime evidence without affecting execution semantics."""
        try:
            self.tracer.emitEvent(
                domain="runtime",
                level=level,
                message=message,
                label=reason,
                attributes={} if attributes is None else attributes,
            )
        except Exception:
            return False
        return True

    def beginInitialization(self) -> None:
        with self._lane:
            if self.applicationRun.state is not ApplicationRunState.CREATED:
                raise RuntimeError("Runtime initialization requires a newly created ApplicationRun.")
            if self._initializing:
                raise RuntimeError("Runtime initialization is already active.")
            self._initializing = True
            self.trace(
                "application-run-initialization-started",
                attributes={
                    "applicationId": self.applicationRun.application.applicationId,
                    "applicationRunId": self.applicationRun.applicationRunId,
                },
            )

    def abortInitialization(self) -> None:
        with self._lane:
            if not self._initializing:
                return
            self._initializing = False
            self.trace(
                "application-run-initialization-aborted",
                attributes={
                    "applicationId": self.applicationRun.application.applicationId,
                    "applicationRunId": self.applicationRun.applicationRunId,
                },
                level="warning",
            )

    def start(self) -> None:
        with self._lane:
            self.applicationRun.start()
            self._initializing = False
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

            errors: list[Exception] = []
            try:
                self.packLoader.close()
            except Exception as err:
                errors.append(err)

            self.trace(
                "application-run-stopped",
                attributes={
                    "applicationId": self.applicationRun.application.applicationId,
                    "applicationRunId": self.applicationRun.applicationRunId,
                },
            )
            self.applicationRun.stop()

            if errors:
                raise ExceptionGroup(
                    "ApplicationRuntime stop reported Pack cleanup errors.",
                    errors,
                )

    def close(self) -> None:
        with self._lane:
            if self._closed:
                return

            errors: list[Exception] = []
            if self.applicationRun.active:
                try:
                    self.stop()
                except Exception as err:
                    errors.append(err)
            else:
                try:
                    self.packLoader.close()
                except Exception as err:
                    errors.append(err)
                try:
                    self.abortInitialization()
                except Exception as err:
                    errors.append(err)

            if self._ownsTracer and not self._tracerClosed:
                try:
                    self.tracer.close()
                except Exception:
                    pass
                self._tracerClosed = True

            self._closed = True
            if errors:
                raise ExceptionGroup(
                    "ApplicationRuntime close reported errors.",
                    errors,
                )

    def requireActive(self) -> None:
        if not self.applicationRun.active:
            raise RuntimeError("ApplicationRun is not active.")

    def requireOperational(self) -> None:
        if not self._initializing and not self.applicationRun.active:
            raise RuntimeError("ApplicationRuntime is neither initializing nor running an active ApplicationRun.")

    def createContext(
        self,
        *,
        identity: CodeEntryIdentity,
        packRoot: Path,
        registrationScope: RegistrationScope,
        allowRegistration: bool = False,
        memoryView: CommittedValueLayer | CommittedValueTransaction | None = None,
        ioView: ManagedIo | ManagedIoTransaction | None = None,
    ) -> CodeEntryContext:
        self.requireOperational()
        return CodeEntryContext(
            identity=identity,
            packRoot=packRoot,
            io=self.io if ioView is None else ioView,
            capabilities=self.capabilities,
            llmProviders=self.llmProviders,
            llmPipeline=self.llmPipeline,
            memory=self.applicationRun.application.committedState if memoryView is None else memoryView,
            registrationScope=registrationScope,
            config=self._config,
            capabilityInvoker=lambda capabilityId, payload=None: self.invokeCapability(
                capabilityId,
                payload,
                memoryView=self.applicationRun.application.committedState if memoryView is None else memoryView,
                ioView=self.io if ioView is None else ioView,
            ),
            allowRegistration=allowRegistration,
        )

    def registerCodeEntry(self, identity: CodeEntryIdentity, packRoot: Path) -> None:
        self.requireOperational()
        if identity.codeEntryInstanceId in self._codeEntries:
            raise RuntimeError(f"CodeEntry instance is already active: {identity.codeEntryInstanceId}.")
        self._codeEntries[identity.codeEntryInstanceId] = (identity, packRoot.resolve())

    def unregisterCodeEntry(self, codeEntryInstanceId: str) -> None:
        self._codeEntries.pop(codeEntryInstanceId, None)

    def invokeCapability(
        self,
        capabilityId: str,
        payload: object | None = None,
        *,
        memoryView: CommittedValueLayer | CommittedValueTransaction | None = None,
        ioView: ManagedIo | ManagedIoTransaction | None = None,
    ) -> object:
        with self._lane:
            self.requireOperational()
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
            context = self.createContext(
                identity=identity,
                packRoot=packRoot,
                registrationScope=scope,
                memoryView=memoryView,
                ioView=ioView,
            )
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
            job.start()
            unit = OrchestrationUnit.mutation(
                applicationRunId=self.applicationRun.applicationRunId,
                transactionBase=self.applicationRun.application.committedState,
            )
            ioTransaction = self.io.openTransaction()
            orchestrationAttributes = {
                "jobId": job.jobId,
                "orchestrationUnitId": unit.orchestrationUnitId,
                "applicationId": self.applicationRun.application.applicationId,
                "applicationRunId": unit.applicationRunId,
                "transactionId": unit.transactionId,
                "ioTransactionId": ioTransaction.ioTransactionId,
                "capabilityId": capabilityId,
                "workKind": "job-capability",
            }
            self.trace(
                "OrchestrationUnitCreated",
                attributes=orchestrationAttributes,
            )
            self.trace(
                "OrchestrationUnitTransactionOpened",
                attributes=orchestrationAttributes,
            )
            unit.start()
            self.trace(
                "OrchestrationUnitStarted",
                attributes=orchestrationAttributes,
            )
            self.trace(
                "job-started",
                attributes=orchestrationAttributes,
            )
            try:
                result = self.invokeCapability(
                    capabilityId,
                    payload,
                    memoryView=unit.memoryView,
                    ioView=ioTransaction,
                )
                unit.commitMutation()
                self.trace(
                    "OrchestrationUnitTransactionCommitted",
                    attributes=orchestrationAttributes,
                )
                ioTransaction.commit()
            except Exception as err:
                mutationWasResolved = unit.mutationResolved
                try:
                    ioTransaction.abort()
                except RuntimeError:
                    pass
                unit.finish(OrchestrationUnitOutcome.FAILED)
                if not mutationWasResolved:
                    self.trace(
                        "OrchestrationUnitTransactionAborted",
                        attributes=orchestrationAttributes,
                    )
                self.trace(
                    "OrchestrationUnitFailed",
                    message=str(err),
                    attributes=orchestrationAttributes,
                    level="error",
                )
                job.fail(err)
                self.trace(
                    "job-failed",
                    message=str(err),
                    attributes=orchestrationAttributes,
                    level="error",
                )
            else:
                unit.finish(OrchestrationUnitOutcome.COMPLETED)
                self.trace(
                    "OrchestrationUnitCompleted",
                    attributes=orchestrationAttributes,
                )
                job.succeed(result)
                self.trace(
                    "job-completed",
                    attributes=orchestrationAttributes,
                )
            return job
