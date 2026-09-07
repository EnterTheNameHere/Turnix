# file: backend/runtime/applicationOperations.py ; version: 2
from __future__ import annotations

from dataclasses import dataclass

from backend.application.lifecycle import ApplicationLifecycle
from backend.packs.runtime import ManualActivationPlan, PackLoader, PackResolver
from backend.runtime.runtimeHost import RuntimeHost
from backend.save import ApplicationStore, LoadedApplicationSave
from backend.tracing import Tracer

__all__ = ["ApplicationRuntimeOperations", "ApplicationRuntimeSession"]


@dataclass(slots=True)
class ApplicationRuntimeSession:
    """One active ApplicationRun plus the Pack graph loaded for that run.

    This is deliberately not RuntimeHost identity. It is the current
    implementation container for one ApplicationRun while RuntimeHost is being
    separated into the DA-28 host-level boundary that may own zero or multiple
    such sessions.
    """

    runtime: RuntimeHost
    packLoader: PackLoader
    loadedSave: LoadedApplicationSave | None = None
    _closed: bool = False

    @property
    def applicationId(self) -> str:
        return self.runtime.applicationRun.application.applicationId

    @property
    def applicationRunId(self) -> str:
        return self.runtime.applicationRun.applicationRunId

    def close(self) -> None:
        if self._closed:
            return
        errors: list[Exception] = []
        try:
            self.packLoader.close()
        except Exception as err:
            errors.append(err)
        try:
            self.runtime.stop()
        except Exception as err:
            errors.append(err)
        self._closed = True
        if errors:
            raise ExceptionGroup("Application runtime session cleanup reported errors.", errors)


class ApplicationRuntimeOperations:
    """Shared create/load operation path used by RuntimeHost entry workflows.

    Startup modes, command surfaces, launchers, tests, and application-specific
    entry scripts must compose these operations rather than owning separate
    Application, SaveBundle, or ApplicationRun lifecycle semantics.

    RuntimeHost is still being migrated toward the DA-28 host-level shape.
    Until that split is complete, RuntimeHost is the concrete one-run runtime
    object instantiated behind this shared operation boundary.
    """

    def __init__(
        self,
        *,
        applicationStore: ApplicationStore,
        packResolver: PackResolver,
    ) -> None:
        if not isinstance(applicationStore, ApplicationStore):
            raise TypeError("applicationStore must be an ApplicationStore.")
        if not isinstance(packResolver, PackResolver):
            raise TypeError("packResolver must be a PackResolver.")
        self._applicationStore = applicationStore
        self._packResolver = packResolver

    def createApplication(
        self,
        *,
        appPackId: str,
        plan: ManualActivationPlan,
        config: dict[str, object] | None = None,
        tracer: Tracer | None = None,
    ) -> ApplicationRuntimeSession:
        runtime = RuntimeHost(
            appPackId=appPackId,
            applicationStore=self._applicationStore,
            config=config,
            tracer=tracer,
        )
        loader = PackLoader(host=runtime, resolver=self._packResolver)
        try:
            ApplicationLifecycle.create(
                host=runtime,
                packLoader=loader,
                plan=plan,
            )
        except Exception:
            self._cleanupFailedOperation(runtime=runtime, loader=loader)
            raise
        return ApplicationRuntimeSession(
            runtime=runtime,
            packLoader=loader,
        )

    def loadApplication(
        self,
        *,
        appPackId: str,
        applicationId: str,
        plan: ManualActivationPlan,
        config: dict[str, object] | None = None,
        tracer: Tracer | None = None,
    ) -> ApplicationRuntimeSession:
        runtime, loaded = RuntimeHost.loadApplication(
            applicationStore=self._applicationStore,
            appPackId=appPackId,
            applicationId=applicationId,
            config=config,
            tracer=tracer,
        )
        loader = PackLoader(host=runtime, resolver=self._packResolver)
        try:
            ApplicationLifecycle.load(
                host=runtime,
                packLoader=loader,
                plan=plan,
            )
        except Exception:
            self._cleanupFailedOperation(runtime=runtime, loader=loader)
            raise
        return ApplicationRuntimeSession(
            runtime=runtime,
            packLoader=loader,
            loadedSave=loaded,
        )

    @staticmethod
    def _cleanupFailedOperation(*, runtime: RuntimeHost, loader: PackLoader) -> None:
        # ApplicationLifecycle already performs best-effort cleanup after it
        # enters lifecycle handling. These calls make the operation boundary
        # idempotently safe for failures that occur before or around that path.
        try:
            loader.close()
        except Exception:
            pass
        try:
            if runtime.applicationRun.active:
                runtime.stop()
            else:
                runtime.abortInitialization()
        except Exception:
            pass
