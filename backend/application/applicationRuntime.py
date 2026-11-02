# file: backend/application/applicationRuntime.py ; version: 20
from __future__ import annotations

from contextlib import suppress
from copy import deepcopy
from threading import RLock
from typing import TYPE_CHECKING

from backend.application.runtime import Application, ApplicationRun, ApplicationRunState
from backend.capabilities.runtime import CapabilityRegistry
from backend.context.codeEntryContext import CodeEntryContext, CodeEntryIdentity
from backend.io.managedIo import ManagedIo, ManagedIoTransaction
from backend.llm.executionSessions import helperCancellation, lifetimeBoundary, jobExecutionBoundary
from backend.llm.providerComposition import invocationBoundary, requireNoPendingComposition
from backend.runtime.executionResources import ExecutionResourceService
from backend.llm.streamingRuntime import LlmProcessingPipeline, LlmProviderRegistry
from backend.observation import ObservationService
from backend.orchestration.cancellation import CancellationSignal, ExecutionCancelled
from backend.orchestration.runtime import Job, JobState, OrchestrationUnit, OrchestrationUnitOutcome
from backend.packs.runtime import PackLoader, PackResolver
from backend.process.configuration import processToolDefinitionsFromConfig
from backend.process.context import ProcessFacade
from backend.process.runtime import ProcessRunner, ProcessToolRegistry
from backend.registration import RegistrationScope
from backend.save import ApplicationStore, LoadedApplicationSave, SaveBundle
from backend.tracing import Tracer, TraceSinkDestination

if TYPE_CHECKING:
    from pathlib import Path

    from backend.values.committed import CommittedValueLayer, CommittedValueTransaction

__all__ = ["ApplicationRuntime"]


class ApplicationRuntime:
    """
    Live Actant execution environment for exactly one ApplicationRun.

    Tracing is evidence only. Trace publication or tracer-close failures are
    deliberately isolated here so loss of observability cannot alter runtime
    lifecycle, capability execution, Job outcome, or authoritative state.

    Host-configured process tools are resolved once for this ApplicationRun and
    exposed to CodeEntry calls only through invocation-bound ``ctx.process``.
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
        observationService: ObservationService | None = None,
        llmExecutionBindings=None,
        providerComposers=(),
        executionResourceService=None,
    ) -> None:
        """
        Creates one ApplicationRun and its mediated runtime services.

        Args:
            appPackId: Defining AppPack identity when creating a new Application.
            application: Existing in-memory Application to run.
            saveBundle: Durable Application snapshot to restore instead.
            applicationStore: Optional durable store used by saveApplication().
            packResolver: Resolver used by this run's PackLoader.
            config: Detached host/application runtime configuration.
            tracer: Optional externally owned tracer.
            observationService: Host-owned shared observation composition. When
                omitted, a standalone runtime composes inactive local observation.

        Raises:
            ValueError: If mutually exclusive or inconsistent Application inputs
                are supplied, or a new Application has no valid AppPack ID.
            TypeError: If the supplied store or resolver has the wrong type, or
                process-tool configuration is structurally invalid.

        """
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
        self.llmExecutions = llmExecutionBindings if llmExecutionBindings is not None else self.llmProviders.executions
        self.llmProviders.executions = self.llmExecutions
        self.providerComposers = tuple(providerComposers)
        self._ownsExecutionResources = executionResourceService is None
        self.executionResources = executionResourceService or ExecutionResourceService()
        self._compositionAttempt = None
        self._llmOwners = {}
        self.lastLlmCleanup = ()
        self._llmClosePrepared = False
        self._config = {} if config is None else deepcopy(config)
        self.processTools = ProcessToolRegistry(processToolDefinitionsFromConfig(self._config))
        self.processRunner = ProcessRunner(self.processTools)
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
            capabilityInvoker=lambda capabilityId, payload=None, memoryView=None: self.invokeCapability(
                capabilityId,
                payload,
                memoryView=memoryView, cancellationSignal=helperCancellation(),
            ),
            trace=lambda reason, attributes: self.trace(reason, attributes=attributes),
        )
        self._observationService = observationService if observationService is not None else ObservationService()
        self._observationOwner = self._observationService.owner()
        self.packLoader = PackLoader(runtime=self, resolver=packResolver)

    @property
    def config(self) -> dict[str, object]:
        """Returns a detached copy of this ApplicationRun's configuration."""
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
        observationService: ObservationService | None = None,
        llmExecutionBindings=None,
        providerComposers=(),
        executionResourceService=None,
    ) -> tuple[ApplicationRuntime, LoadedApplicationSave]:
        """
        Creates a fresh ApplicationRuntime from one durable Application root snapshot.

        Loading restores only the SaveBundle's committed root. No transaction
        hierarchy exists in the new runtime. AppPack lifecycle hooks are a later
        layer above this storage operation.
        """
        if not isinstance(applicationStore, ApplicationStore):
            raise TypeError("applicationStore must be an ApplicationStore.")
        loaded = applicationStore.load(appPackId=appPackId, applicationId=applicationId)
        runtime = cls(
            saveBundle=loaded.bundle,
            applicationStore=applicationStore,
            packResolver=packResolver,
            config=config,
            tracer=tracer,
            observationService=observationService, llmExecutionBindings=llmExecutionBindings,
            providerComposers=providerComposers, executionResourceService=executionResourceService,
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
            return self._saveBundle.nextGeneration(committedState=application.committedState)
        return self._saveBundle.advanceToGeneration(
            generation=targetGeneration,
            committedState=application.committedState,
        )

    def _acceptSaveBundle(self, bundle: SaveBundle) -> None:
        """Accepts an already captured or published SaveBundle identity."""
        self._saveBundle = bundle
        application = self.applicationRun.application
        application.saveBundleId = bundle.saveBundleId
        application.durableGeneration = bundle.generation

    def saveApplication(self, applicationStore: ApplicationStore | None = None) -> SaveBundle:
        """
        Persists exactly one snapshot of the current authoritative root.

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
        """
        Captures the next in-memory SaveBundle generation for this Application.

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
        except Exception:  # noqa: BLE001 - tracing failure must not alter runtime semantics.
            return False
        return True

    def beginInitialization(self) -> None:
        """Opens the initialization phase for a fresh ApplicationRun."""
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
        """Ends an active initialization phase without starting the run."""
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
        """Starts the initialized ApplicationRun and closes initialization."""
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

    @lifetimeBoundary
    def stop(self, *, _deferLlmClose=False) -> None:
        """Stops an active run after attempting Pack cleanup."""
        if not _deferLlmClose:
            self.prepareLlmClose()
        else:
            self._llmClosePrepared = True
        with self._lane:
            for codeEntryId in tuple(self._llmOwners):
                self.revokeLlmCodeEntry(codeEntryId)
            if not self.applicationRun.active:
                return

            self._observationOwner.release()
            errors: list[Exception] = []
            try:
                self.packLoader.close()
            except Exception as err:  # noqa: BLE001 - Pack cleanup may execute arbitrary Pack code.
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

    @lifetimeBoundary
    def close(self, *, _deferLlmClose=False) -> None:
        """Closes Pack/runtime resources and the internally owned tracer once."""
        if not _deferLlmClose:
            self.prepareLlmClose()
        else:
            self._llmClosePrepared = True
        with self._lane:
            for codeEntryId in tuple(self._llmOwners):
                self.revokeLlmCodeEntry(codeEntryId)
            if self._closed:
                return

            self._observationOwner.release()
            errors: list[Exception] = []
            if self.applicationRun.active:
                try:
                    self.stop(_deferLlmClose=True)
                except Exception as err:  # noqa: BLE001 - runtime cleanup may cross arbitrary Pack code.
                    errors.append(err)
            else:
                try:
                    self.packLoader.close()
                except Exception as err:  # noqa: BLE001 - Pack cleanup may execute arbitrary Pack code.
                    errors.append(err)
                try:
                    self.abortInitialization()
                except Exception as err:  # noqa: BLE001 - initialization cleanup is aggregated best-effort.
                    errors.append(err)

            if self._ownsTracer and not self._tracerClosed:
                with suppress(Exception):
                    self.tracer.close()
                self._tracerClosed = True

            self._closed = True
            if errors:
                raise ExceptionGroup(
                    "ApplicationRuntime close reported errors.",
                    errors,
                )

    def prepareLlmClose(self, *, permanent=True):
        """Revokes and attempts controlled stops before acquiring the runtime lane.

        Pending calls keep the runtime intact for explicit retry; this does not
        wait, recover unknown provider state, or change Job cancellation outcomes.
        """
        requireNoPendingComposition(self)
        self.llmExecutions.revokeRegistry(self.llmProviders, permanent=permanent)
        try:
            self.llmExecutions.finishClose(self.llmProviders)
        except ExceptionGroup:
            pass  # Final lifetime boundary reports retained cleanup evidence.
        if self.llmExecutions.pendingCalls(self.llmProviders):
            from backend.llm.errors import LlmExecutionCleanupError
            raise LlmExecutionCleanupError()
        self._llmClosePrepared = True

    def requireCompositionReady(self):
        """Exclude invocation through leaked references before trusted composition."""
        with self._lane:
            attempt = self._compositionAttempt
            if attempt is not None and (attempt.revoked or attempt.phase != "running"):
                raise RuntimeError("Application activation has not completed provider composition.")

    def requireActive(self) -> None:
        """Requires this runtime to own an active ApplicationRun."""
        if not self.applicationRun.active:
            raise RuntimeError("ApplicationRun is not active.")

    def requireOperational(self) -> None:
        """Requires initialization or active-run authority for runtime work."""
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
        cancellationSignal: CancellationSignal | None = None,
        allowExecution: bool = False,
    ) -> CodeEntryContext:
        """Creates one fresh call-specific CodeEntryContext with explicit facilities."""
        self.requireOperational()
        contextReference: CodeEntryContext | None = None

        def requireContextValid() -> None:
            """Delegates process-facade lifetime checks to the constructed context."""
            if contextReference is None:
                raise RuntimeError("CodeEntryContext process authority is not initialized.")
            contextReference.requireValid()

        process = ProcessFacade(
            runner=self.processRunner,
            requireValid=requireContextValid,
        )
        context = CodeEntryContext(
            identity=identity,
            packRoot=packRoot,
            io=self.io if ioView is None else ioView,
            capabilities=self.capabilities,
            llmProviders=self.llmProviders,
            llmPipeline=self.llmPipeline,
            observationOwner=self._observationOwner,
            executionAccess=self._llmOwners[identity.codeEntryInstanceId].invocation(cancellationSignal) if allowExecution else None,
            process=process,
            memory=self.applicationRun.application.committedState if memoryView is None else memoryView,
            registrationScope=registrationScope,
            config=self._config,
            capabilityInvoker=lambda capabilityId, payload=None: self.invokeCapability(
                capabilityId,
                payload,
                memoryView=self.applicationRun.application.committedState if memoryView is None else memoryView,
                ioView=self.io if ioView is None else ioView,
                cancellationSignal=cancellationSignal,
            ),
            allowRegistration=allowRegistration,
            cancellationSignal=cancellationSignal,
        )
        contextReference = context
        return context

    def registerCodeEntry(self, identity: CodeEntryIdentity, packRoot: Path) -> None:
        """Registers one loaded CodeEntry as eligible for runtime invocation."""
        self.requireOperational()
        if identity.codeEntryInstanceId in self._codeEntries:
            raise RuntimeError(f"CodeEntry instance is already active: {identity.codeEntryInstanceId}.")
        self._codeEntries[identity.codeEntryInstanceId] = (identity, packRoot.resolve())
        self._llmOwners[identity.codeEntryInstanceId] = self.llmExecutions.owner(self.llmProviders)
        self._llmClosePrepared = False

    def unregisterCodeEntry(self, codeEntryInstanceId: str) -> None:
        """Withdraws one loaded CodeEntry instance from runtime invocation."""
        self.revokeLlmCodeEntry(codeEntryInstanceId)
        self._codeEntries.pop(codeEntryInstanceId, None)

    def revokeLlmCodeEntry(self, codeEntryInstanceId):
        """Revokes exact owner/provider authority before withdrawal, without callbacks."""
        owner = self._llmOwners.get(codeEntryInstanceId)
        if owner is not None:
            owner.revoke()
        self.llmExecutions.revokeRegistration(self.llmProviders, codeEntryInstanceId)

    @invocationBoundary
    @lifetimeBoundary
    def invokeCapability(
        self,
        capabilityId: str,
        payload: object | None = None,
        *,
        memoryView: CommittedValueLayer | CommittedValueTransaction | None = None,
        ioView: ManagedIo | ManagedIoTransaction | None = None,
        cancellationSignal: CancellationSignal | None = None,
    ) -> object:
        """Invokes one resolved capability with a fresh call-specific context."""
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
                cancellationSignal=cancellationSignal, allowExecution=True,
            )
            try:
                result = self.capabilities.invokeResolved(registration, context=context, payload=payload)
            except ExecutionCancelled:
                self.trace(
                    "capability-invocation-cancelled",
                    attributes={"capabilityId": capabilityId, "ownerId": registration.ownerId},
                )
                raise
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

    @lifetimeBoundary
    @invocationBoundary
    @jobExecutionBoundary
    def runJob(
        self,
        capabilityId: str,
        payload: object | None = None,
        *,
        job: Job | None = None,
    ) -> Job:
        """
        Runs one serialized mutating capability Job with cancellation-aware boundaries.

        Supplying a Pending Job lets the caller retain its stable handle before this
        blocking execution begins and request cancellation from another thread.
        Existing callers may omit job and receive the internally created handle after
        execution as before.
        """
        with self._lane:
            self.requireActive()
            activeJob = Job.new() if job is None else job
            if not isinstance(activeJob, Job):
                raise TypeError("job must be a Job or None.")
            if activeJob.state is not JobState.CANCELLED:
                activeJob.start()
            if activeJob.state is JobState.CANCELLED:
                self.trace(
                    "job-cancelled",
                    attributes={
                        "jobId": activeJob.jobId,
                        "applicationId": self.applicationRun.application.applicationId,
                        "applicationRunId": self.applicationRun.applicationRunId,
                        "capabilityId": capabilityId,
                        "phase": "before-start",
                    },
                )
                return activeJob

            unit = OrchestrationUnit.mutation(
                applicationRunId=self.applicationRun.applicationRunId,
                transactionBase=self.applicationRun.application.committedState,
            )
            ioTransaction = self.io.openTransaction()
            orchestrationAttributes = {
                "jobId": activeJob.jobId,
                "orchestrationUnitId": unit.orchestrationUnitId,
                "applicationId": self.applicationRun.application.applicationId,
                "applicationRunId": unit.applicationRunId,
                "transactionId": unit.transactionId,
                "ioTransactionId": ioTransaction.ioTransactionId,
                "capabilityId": capabilityId,
                "workKind": "job-capability",
            }
            self.trace("OrchestrationUnitCreated", attributes=orchestrationAttributes)
            self.trace("OrchestrationUnitTransactionOpened", attributes=orchestrationAttributes)
            self.trace("managed-io-transaction-opened", attributes=orchestrationAttributes)
            unit.start()
            self.trace("OrchestrationUnitStarted", attributes=orchestrationAttributes)
            self.trace("job-started", attributes=orchestrationAttributes)
            try:
                activeJob.cancellationSignal.raiseIfRequested()
                result = self.invokeCapability(
                    capabilityId,
                    payload,
                    memoryView=unit.memoryView,
                    ioView=ioTransaction,
                    cancellationSignal=activeJob.cancellationSignal,
                )
                activeJob.cancellationSignal.raiseIfRequested()
                unit.commitMutation()
                activeJob.authoritativeStateAccepted = True
                self.trace("OrchestrationUnitTransactionCommitted", attributes=orchestrationAttributes)
                try:
                    ioTransaction.commit()
                except Exception as err:
                    self.trace(
                        "managed-io-transaction-failed",
                        message=str(err),
                        attributes=orchestrationAttributes,
                        level="error",
                    )
                    raise
                self.trace("managed-io-transaction-committed", attributes=orchestrationAttributes)
            except ExecutionCancelled:
                mutationWasResolved = unit.mutationResolved
                try:
                    ioTransaction.abort()
                except RuntimeError:
                    pass
                else:
                    self.trace("managed-io-transaction-aborted", attributes=orchestrationAttributes)
                unit.finish(OrchestrationUnitOutcome.CANCELLED)
                if not mutationWasResolved:
                    self.trace("OrchestrationUnitTransactionAborted", attributes=orchestrationAttributes)
                self.trace("OrchestrationUnitCancelled", attributes=orchestrationAttributes)
                activeJob.cancel()
                self.trace("job-cancelled", attributes=orchestrationAttributes)
            except Exception as err:  # noqa: BLE001 - capability failure may originate from arbitrary Pack code.
                mutationWasResolved = unit.mutationResolved
                try:
                    ioTransaction.abort()
                except RuntimeError:
                    pass
                else:
                    self.trace("managed-io-transaction-aborted", attributes=orchestrationAttributes)
                unit.finish(OrchestrationUnitOutcome.FAILED)
                if not mutationWasResolved:
                    self.trace("OrchestrationUnitTransactionAborted", attributes=orchestrationAttributes)
                self.trace(
                    "OrchestrationUnitFailed",
                    message=str(err),
                    attributes=orchestrationAttributes,
                    level="error",
                )
                activeJob.fail(err)
                self.trace(
                    "job-failed",
                    message=str(err),
                    attributes=orchestrationAttributes,
                    level="error",
                )
            else:
                unit.finish(OrchestrationUnitOutcome.COMPLETED)
                self.trace("OrchestrationUnitCompleted", attributes=orchestrationAttributes)
                activeJob.succeed(result)
                self.trace("job-completed", attributes=orchestrationAttributes)
            return activeJob
