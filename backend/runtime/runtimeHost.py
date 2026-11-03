# file: backend/runtime/runtimeHost.py ; version: 13
from __future__ import annotations

from contextlib import suppress
from enum import StrEnum
from threading import RLock

from backend.application.applicationRuntime import ApplicationRuntime
from backend.application.lifecycle import ApplicationLifecycle
from backend.core.runtimeIds import newRuntimeId
from backend.llm.errors import LlmExecutionCleanupError
from backend.llm.executionSessions import LlmExecutionBindings, lifetimeBoundary, hostClosePreflight
from backend.llm.providerComposition import finishResourceEffects, finishBrokerEffects
from backend.observation import ObservationService
from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.runtime.executionResources import ExecutionResourceService
from backend.runtime.sharedServices import (
    SharedServiceRegistry,
    bindApplicationRunSharedServices,
    unbindApplicationRunSharedServices,
)
from backend.save import ApplicationStore
from backend.telemetry import TelemetryService
from backend.tracing import Tracer, TraceSinkDestination

__all__ = ["RuntimeHost", "RuntimeHostState"]


class RuntimeHostState(StrEnum):
    """Lifecycle states for one RuntimeHost instance."""

    CREATED = "Created"
    ACTIVE = "Active"
    STOPPED = "Stopped"


class RuntimeHost:
    """
    Host-level Actant runtime boundary owning zero or more ApplicationRuntimes.

    RuntimeHost is not an Application or ApplicationRun. It coordinates shared
    host-facing services and owns the lifetime registry for live
    ApplicationRuntimes. Application-specific execution authority remains in
    each ApplicationRuntime and its ApplicationRun.
    """

    def __init__(
        self,
        *,
        applicationStore: ApplicationStore,
        packResolver: PackResolver,
        tracer: Tracer | None = None,
        telemetryService: TelemetryService | None = None,
        providerComposers=(),
    ) -> None:
        """Creates a host with inactive shared observation infrastructure.

        telemetryService is a host-selected borrowed collector; unrelated direct
        claims survive host teardown. Omission selects lazy installed providers
        without activating optional Packs or touching hardware at construction.
        """
        if not isinstance(applicationStore, ApplicationStore):
            raise TypeError("applicationStore must be an ApplicationStore.")
        if not isinstance(packResolver, PackResolver):
            raise TypeError("packResolver must be a PackResolver.")

        self.runtimeHostId = newRuntimeId()
        self.state = RuntimeHostState.CREATED
        self._applicationStore = applicationStore
        self._packResolver = packResolver
        self._lane = RLock()
        self._runtimesByRunId: dict[str, ApplicationRuntime] = {}
        self._runIdByApplicationId: dict[str, str] = {}
        self.providerComposers = tuple(providerComposers)
        self._pendingInitializations = {}
        self._initializingApplicationIds = set()
        self.sharedServices = SharedServiceRegistry()
        self.executionResources = ExecutionResourceService()
        self.llmExecutions = LlmExecutionBindings()
        self.lastLlmCleanup = ()
        self._stopping = False
        self._observations = ObservationService(telemetryService)
        self._ownsTracer = tracer is None
        self.tracer = tracer or Tracer(
            origin="actant.runtime-host",
            destinations=(TraceSinkDestination(),),
        )

    @property
    def activeApplicationRuntimes(self) -> tuple[ApplicationRuntime, ...]:
        """Returns the live ApplicationRuntimes currently owned by this host."""
        with self._lane:
            return tuple(self._runtimesByRunId.values())

    def start(self) -> None:
        """Transitions this RuntimeHost into its active lifetime once."""
        with self._lane:
            if self.state is RuntimeHostState.ACTIVE:
                raise RuntimeError("RuntimeHost is already active.")
            if self.state is RuntimeHostState.STOPPED:
                raise RuntimeError("A stopped RuntimeHost cannot be restarted.")
            self.state = RuntimeHostState.ACTIVE
            self.trace("runtime-host-started")

    def requireActive(self) -> None:
        """Requires this RuntimeHost to be active before host operations proceed."""
        if self.state is not RuntimeHostState.ACTIVE or self._stopping:
            raise RuntimeError("RuntimeHost is not active.")

    @lifetimeBoundary
    def createApplication(
        self,
        *,
        appPackId: str,
        plan: ManualActivationPlan,
        config: dict[str, object] | None = None,
        tracer: Tracer | None = None,
    ) -> ApplicationRuntime:
        """Creates, initializes, starts, and registers one new ApplicationRuntime."""
        return self._initialize(appPackId=appPackId, plan=plan, config=config, tracer=tracer)

    @lifetimeBoundary
    def loadApplication(
        self,
        *,
        appPackId: str,
        applicationId: str,
        plan: ManualActivationPlan,
        config: dict[str, object] | None = None,
        tracer: Tracer | None = None,
    ) -> ApplicationRuntime:
        """Loads, initializes, starts, and registers one durable ApplicationRuntime."""
        return self._initialize(appPackId=appPackId, plan=plan, config=config,
                                tracer=tracer, applicationId=applicationId)

    def _initialize(self, *, appPackId, plan, config, tracer, applicationId=None):
        """Compose a private initialization outside lanes before hooks or publication."""
        runtime = None
        loaded = None
        try:
            with self._lane:
                self.requireActive()
                if applicationId is not None and (applicationId in self._runIdByApplicationId
                        or applicationId in self._initializingApplicationIds):
                    raise RuntimeError("Application is already active or initializing.")
                options = dict(applicationStore=self._applicationStore, packResolver=self._packResolver,
                    config=config, tracer=tracer, observationService=self._observations,
                    llmExecutionBindings=self.llmExecutions, providerComposers=self.providerComposers,
                    executionResourceService=self.executionResources)
                if applicationId is None:
                    runtime = ApplicationRuntime(appPackId=appPackId, **options)
                else:
                    runtime, loaded = ApplicationRuntime.loadApplication(appPackId=appPackId,
                        applicationId=applicationId, **options)
                runId = runtime.applicationRun.applicationRunId
                appId = runtime.applicationRun.application.applicationId
                self._initializingApplicationIds.add(appId)
                self._pendingInitializations[runId] = runtime
                bindApplicationRunSharedServices(runId, self.sharedServices)
                attempt = ApplicationLifecycle.prepare(runtime=runtime, plan=plan,
                    mode="create" if applicationId is None else "load")
            attempt.compose()
            with self._lane:
                self.requireActive()
                attempt.requireCurrent()
                ApplicationLifecycle.resume(attempt)
                self._register(runtime)
                self._pendingInitializations.pop(runId, None)
                self._initializingApplicationIds.discard(appId)
                attributes = self._runtimeIdentity(runtime)
                if loaded is not None:
                    attributes.update(loadedGeneration=loaded.bundle.generation, durableGeneration=loaded.durableGeneration)
                self.trace("application-runtime-created" if loaded is None else "application-runtime-loaded", attributes=attributes)
                return runtime
        except Exception as primary:
            if runtime is not None:
                try:
                    ApplicationLifecycle.abort(runtime, primary)
                finally:
                    with self._lane:
                        if runtime._compositionAttempt is None:
                            self._pendingInitializations.pop(runtime.applicationRun.applicationRunId, None)
                            self._initializingApplicationIds.discard(runtime.applicationRun.application.applicationId)
                            unbindApplicationRunSharedServices(runtime.applicationRun.applicationRunId)
            raise

    def applicationRuntime(self, applicationRunId: str) -> ApplicationRuntime:
        """Returns one active ApplicationRuntime by its ApplicationRun identity."""
        if type(applicationRunId) is not str or not applicationRunId:
            raise ValueError("applicationRunId must be a non-empty string.")
        with self._lane:
            try:
                return self._runtimesByRunId[applicationRunId]
            except KeyError as err:
                raise LookupError(
                    f"ApplicationRun is not active in this RuntimeHost: {applicationRunId}.",
                ) from err

    @lifetimeBoundary
    @hostClosePreflight
    def closeApplicationRun(self, applicationRunId: str) -> None:
        """Closes and unregisters one active ApplicationRun from this RuntimeHost."""
        if type(applicationRunId) is not str or not applicationRunId:
            raise ValueError("applicationRunId must be a non-empty string.")
        with self._lane:
            self.requireActive()
            try:
                runtime = self._runtimesByRunId.pop(applicationRunId)
            except KeyError as err:
                raise LookupError(
                    f"ApplicationRun is not active in this RuntimeHost: {applicationRunId}.",
                ) from err

            applicationId = runtime.applicationRun.application.applicationId
            self._runIdByApplicationId.pop(applicationId, None)
            errors = self._closeRuntime(runtime)
            unbindApplicationRunSharedServices(applicationRunId)
            self.trace(
                "application-runtime-closed",
                attributes=self._runtimeIdentity(runtime),
            )
            if errors:
                raise ExceptionGroup(
                    "ApplicationRuntime close reported errors.",
                    errors,
                )

    @lifetimeBoundary
    @hostClosePreflight
    def stop(self) -> None:
        """Closes host admission and reports unresolved resource cleanup honestly.

        Legacy application/shared-service teardown retains its existing host-lock
        behavior. New execution-resource callbacks run after leaving that lock.
        Repeated stop still attempts deferred drain and reports unknown/failure.
        """
        errors: list[Exception] = []
        with self._lane:
            runtimes = tuple(self._runtimesByRunId.values())
            self.executionResources.close()
            if self.state is not RuntimeHostState.STOPPED:
                try:
                    for applicationRunId in tuple(self._runtimesByRunId):
                        runtime = self._runtimesByRunId.pop(applicationRunId)
                        applicationId = runtime.applicationRun.application.applicationId
                        self._runIdByApplicationId.pop(applicationId, None)
                        errors.extend(self._closeRuntime(runtime))
                        unbindApplicationRunSharedServices(applicationRunId)

                    self._observations.close()
                    try:
                        self.sharedServices.close()
                    except Exception as err:  # noqa: BLE001 - preserve aggregate legacy cleanup.
                        errors.append(err)
                    self.state = RuntimeHostState.STOPPED
                    self.trace("runtime-host-stopped")
                    if self._ownsTracer:
                        with suppress(Exception):
                            self.tracer.close()
                except Exception as err:  # noqa: BLE001 - new cleanup must also run on legacy failure.
                    errors.append(err)

        # Queue revocation may have completed operations during legacy teardown.
        # First stage can remain pending until ER-01 drains its registered pins.
        with suppress(LlmExecutionCleanupError, ExceptionGroup):
            finishBrokerEffects(self.llmExecutions, self.executionResources, runtimes)
        try:
            finishResourceEffects(self.executionResources)
        except Exception as err:  # noqa: BLE001 - include pending/failed/unknown resource evidence.
            errors.append(err)
        if errors:
            raise ExceptionGroup("RuntimeHost shutdown reported cleanup errors.", errors)

    def trace(
        self,
        reason: str,
        *,
        message: str = "",
        attributes: dict[str, object] | None = None,
        level: str = "info",
    ) -> bool:
        """Attempts to emit host evidence without affecting runtime semantics."""
        try:
            self.tracer.emitEvent(
                domain="runtime-host",
                level=level,
                message=message,
                label=reason,
                attributes={
                    "runtimeHostId": self.runtimeHostId,
                    **({} if attributes is None else attributes),
                },
            )
        except Exception:  # noqa: BLE001 - observability failure is deliberately isolated.
            return False
        return True

    def _register(self, runtime: ApplicationRuntime) -> None:
        """Registers one successfully initialized ApplicationRuntime in this host."""
        application = runtime.applicationRun.application
        applicationRunId = runtime.applicationRun.applicationRunId
        applicationId = application.applicationId

        if applicationRunId in self._runtimesByRunId:
            raise RuntimeError(
                f"ApplicationRun is already registered in this RuntimeHost: {applicationRunId}.",
            )
        if applicationId in self._runIdByApplicationId:
            raise RuntimeError(
                f"Application is already active in this RuntimeHost: {applicationId}.",
            )

        self._runtimesByRunId[applicationRunId] = runtime
        self._runIdByApplicationId[applicationId] = applicationRunId

    @staticmethod
    def _runtimeIdentity(runtime: ApplicationRuntime) -> dict[str, object]:
        """Returns stable host trace identity for one ApplicationRuntime."""
        application = runtime.applicationRun.application
        return {
            "appPackId": application.appPackId,
            "applicationId": application.applicationId,
            "applicationRunId": runtime.applicationRun.applicationRunId,
        }

    @staticmethod
    def _closeRuntime(runtime: ApplicationRuntime) -> list[Exception]:
        """Closes one ApplicationRuntime and returns cleanup errors as evidence."""
        try:
            runtime.close(_deferLlmClose=True)
        except Exception as err:  # noqa: BLE001 - Pack/runtime cleanup may report arbitrary failures.
            return [err]
        return []

    @staticmethod
    def _cleanupFailedOperation(
        *,
        runtime: ApplicationRuntime,
    ) -> None:
        """Best-effort closes a runtime whose create/load operation failed."""
        with suppress(Exception):
            runtime.close(_deferLlmClose=True)
