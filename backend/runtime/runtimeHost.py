# file: backend/runtime/runtimeHost.py ; version: 7
from __future__ import annotations

from contextlib import suppress
from enum import StrEnum
from threading import RLock

from backend.application.applicationRuntime import ApplicationRuntime
from backend.application.lifecycle import ApplicationLifecycle
from backend.core.runtimeIds import newRuntimeId
from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.runtime.sharedServices import (
    SharedServiceRegistry,
    bindApplicationRunSharedServices,
    unbindApplicationRunSharedServices,
)
from backend.save import ApplicationStore
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
    ) -> None:
        """Creates one RuntimeHost around shared storage, Pack resolution, and services."""
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
        self.sharedServices = SharedServiceRegistry()
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
        if self.state is not RuntimeHostState.ACTIVE:
            raise RuntimeError("RuntimeHost is not active.")

    def createApplication(
        self,
        *,
        appPackId: str,
        plan: ManualActivationPlan,
        config: dict[str, object] | None = None,
        tracer: Tracer | None = None,
    ) -> ApplicationRuntime:
        """Creates, initializes, starts, and registers one new ApplicationRuntime."""
        with self._lane:
            self.requireActive()
            runtime = ApplicationRuntime(
                appPackId=appPackId,
                applicationStore=self._applicationStore,
                packResolver=self._packResolver,
                config=config,
                tracer=tracer,
            )
            applicationRunId = runtime.applicationRun.applicationRunId
            bindApplicationRunSharedServices(applicationRunId, self.sharedServices)
            try:
                ApplicationLifecycle.create(
                    runtime=runtime,
                    plan=plan,
                )
                self._register(runtime)
            except Exception:  # noqa: BLE001 - lifecycle failure may originate from arbitrary Pack code.
                self._cleanupFailedOperation(runtime=runtime)
                unbindApplicationRunSharedServices(applicationRunId)
                raise

            self.trace(
                "application-runtime-created",
                attributes=self._runtimeIdentity(runtime),
            )
            return runtime

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
        with self._lane:
            self.requireActive()
            if applicationId in self._runIdByApplicationId:
                raise RuntimeError(
                    f"Application is already active in this RuntimeHost: {applicationId}.",
                )

            runtime, loaded = ApplicationRuntime.loadApplication(
                applicationStore=self._applicationStore,
                appPackId=appPackId,
                applicationId=applicationId,
                packResolver=self._packResolver,
                config=config,
                tracer=tracer,
            )
            applicationRunId = runtime.applicationRun.applicationRunId
            bindApplicationRunSharedServices(applicationRunId, self.sharedServices)
            try:
                ApplicationLifecycle.load(
                    runtime=runtime,
                    plan=plan,
                )
                self._register(runtime)
            except Exception:  # noqa: BLE001 - lifecycle failure may originate from arbitrary Pack code.
                self._cleanupFailedOperation(runtime=runtime)
                unbindApplicationRunSharedServices(applicationRunId)
                raise

            self.trace(
                "application-runtime-loaded",
                attributes={
                    **self._runtimeIdentity(runtime),
                    "loadedGeneration": loaded.bundle.generation,
                    "durableGeneration": loaded.durableGeneration,
                },
            )
            return runtime

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

    def stop(self) -> None:
        """Closes all ApplicationRuntimes, shared host services, and owned tracing."""
        with self._lane:
            if self.state is RuntimeHostState.STOPPED:
                return

            errors: list[Exception] = []
            for applicationRunId in tuple(self._runtimesByRunId):
                runtime = self._runtimesByRunId.pop(applicationRunId)
                applicationId = runtime.applicationRun.application.applicationId
                self._runIdByApplicationId.pop(applicationId, None)
                errors.extend(self._closeRuntime(runtime))
                unbindApplicationRunSharedServices(applicationRunId)

            try:
                self.sharedServices.close()
            except Exception as err:  # noqa: BLE001 - host shutdown aggregates arbitrary service failures.
                errors.append(err)

            self.state = RuntimeHostState.STOPPED
            self.trace("runtime-host-stopped")
            if self._ownsTracer:
                with suppress(Exception):
                    self.tracer.close()

            if errors:
                raise ExceptionGroup(
                    "RuntimeHost shutdown reported cleanup errors.",
                    errors,
                )

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
            runtime.close()
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
            runtime.close()
