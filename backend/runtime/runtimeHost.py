# file: backend/runtime/runtimeHost.py ; version: 4
from __future__ import annotations

from enum import StrEnum
from threading import RLock

from backend.application.applicationRuntime import ApplicationRuntime
from backend.application.lifecycle import ApplicationLifecycle
from backend.core.runtimeIds import newRuntimeId
from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.save import ApplicationStore
from backend.tracing import TraceSinkDestination, Tracer

__all__ = ["RuntimeHost", "RuntimeHostState"]


class RuntimeHostState(StrEnum):
    CREATED = "Created"
    ACTIVE = "Active"
    STOPPED = "Stopped"


class RuntimeHost:
    """Host-level Actant runtime boundary owning zero or more ApplicationRuntimes.

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
        self._ownsTracer = tracer is None
        self.tracer = tracer or Tracer(
            origin="actant.runtime-host",
            destinations=(TraceSinkDestination(),),
        )

    @property
    def activeApplicationRuntimes(self) -> tuple[ApplicationRuntime, ...]:
        with self._lane:
            return tuple(self._runtimesByRunId.values())

    def start(self) -> None:
        with self._lane:
            if self.state is RuntimeHostState.ACTIVE:
                raise RuntimeError("RuntimeHost is already active.")
            if self.state is RuntimeHostState.STOPPED:
                raise RuntimeError("A stopped RuntimeHost cannot be restarted.")
            self.state = RuntimeHostState.ACTIVE
            self.trace("runtime-host-started")

    def requireActive(self) -> None:
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
        with self._lane:
            self.requireActive()
            runtime = ApplicationRuntime(
                appPackId=appPackId,
                applicationStore=self._applicationStore,
                packResolver=self._packResolver,
                config=config,
                tracer=tracer,
            )
            try:
                ApplicationLifecycle.create(
                    runtime=runtime,
                    plan=plan,
                )
                self._register(runtime)
            except Exception:
                self._cleanupFailedOperation(runtime=runtime)
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
            try:
                ApplicationLifecycle.load(
                    runtime=runtime,
                    plan=plan,
                )
                self._register(runtime)
            except Exception:
                self._cleanupFailedOperation(runtime=runtime)
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
        with self._lane:
            if self.state is RuntimeHostState.STOPPED:
                return

            errors: list[Exception] = []
            for applicationRunId in tuple(self._runtimesByRunId):
                runtime = self._runtimesByRunId.pop(applicationRunId)
                applicationId = runtime.applicationRun.application.applicationId
                self._runIdByApplicationId.pop(applicationId, None)
                errors.extend(self._closeRuntime(runtime))

            self.state = RuntimeHostState.STOPPED
            self.trace("runtime-host-stopped")
            if self._ownsTracer:
                try:
                    self.tracer.close()
                except Exception:
                    pass

            if errors:
                raise ExceptionGroup(
                    "RuntimeHost shutdown reported ApplicationRuntime cleanup errors.",
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
        except Exception:
            return False
        return True

    def _register(self, runtime: ApplicationRuntime) -> None:
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
        application = runtime.applicationRun.application
        return {
            "appPackId": application.appPackId,
            "applicationId": application.applicationId,
            "applicationRunId": runtime.applicationRun.applicationRunId,
        }

    @staticmethod
    def _closeRuntime(runtime: ApplicationRuntime) -> list[Exception]:
        try:
            runtime.close()
        except Exception as err:
            return [err]
        return []

    @staticmethod
    def _cleanupFailedOperation(
        *,
        runtime: ApplicationRuntime,
    ) -> None:
        try:
            runtime.close()
        except Exception:
            pass
