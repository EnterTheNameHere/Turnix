# file: backend/application/lifecycle.py ; version: 2
from __future__ import annotations

from backend.packs.runtime import ManualActivationPlan, PackLoader
from backend.application.applicationRuntime import ApplicationRuntime
from backend.save import SaveBundle

__all__ = ["ApplicationLifecycle"]


class ApplicationLifecycle:
    """Coordinates Pack activation, Application hooks, and persistence order.

    Storage representation remains ApplicationRuntime/ApplicationStore responsibility,
    while PackLoader owns mechanical CodeEntry hook invocation. This coordinator
    supplies the semantic ordering between those boundaries.
    """

    @classmethod
    def create(
        cls,
        *,
        runtime: ApplicationRuntime,
        packLoader: PackLoader,
        plan: ManualActivationPlan,
    ) -> SaveBundle:
        """Creates and publishes a new persistent Application, then starts its run."""
        if runtime.acceptedSaveBundle is not None:
            raise RuntimeError("Application creation requires a ApplicationRuntime with no accepted SaveBundle.")

        runtime.beginInitialization()
        try:
            packLoader.activate(plan)

            root = runtime.applicationRun.application.committedState
            creationTransaction = root.openTransaction()
            try:
                packLoader.invokeApplicationCreate(memoryView=creationTransaction)
                creationTransaction.commit()
            except Exception:
                try:
                    creationTransaction.abort()
                except RuntimeError:
                    pass
                raise

            accepted = runtime.saveApplication()

            beforeLoad = root.snapshot()
            packLoader.invokeApplicationLoad()
            if root.snapshot() != beforeLoad:
                accepted = runtime.saveApplication()

            runtime.start()
            packLoader.invokeApplicationRun()
            return accepted
        except Exception as lifecycleError:
            cleanupErrors = cls._cleanupFailedStart(runtime=runtime, packLoader=packLoader)
            if cleanupErrors:
                raise ExceptionGroup(
                    "Application creation failed and runtime cleanup also reported errors.",
                    [lifecycleError, *cleanupErrors],
                ) from None
            raise

    @classmethod
    def load(
        cls,
        *,
        runtime: ApplicationRuntime,
        packLoader: PackLoader,
        plan: ManualActivationPlan,
    ) -> SaveBundle:
        """Runs loaded-Application lifecycle and starts a fresh ApplicationRun."""
        accepted = runtime.acceptedSaveBundle
        if accepted is None:
            raise RuntimeError("Application loading requires a ApplicationRuntime restored from an accepted SaveBundle.")

        runtime.beginInitialization()
        try:
            packLoader.activate(plan)

            root = runtime.applicationRun.application.committedState
            beforeLoad = root.snapshot()
            packLoader.invokeApplicationLoad()
            if root.snapshot() != beforeLoad:
                accepted = runtime.saveApplication()

            runtime.start()
            packLoader.invokeApplicationRun()
            return accepted
        except Exception as lifecycleError:
            cleanupErrors = cls._cleanupFailedStart(runtime=runtime, packLoader=packLoader)
            if cleanupErrors:
                raise ExceptionGroup(
                    "Application load failed and runtime cleanup also reported errors.",
                    [lifecycleError, *cleanupErrors],
                ) from None
            raise

    @staticmethod
    def _cleanupFailedStart(
        *,
        runtime: ApplicationRuntime,
        packLoader: PackLoader,
    ) -> list[Exception]:
        errors: list[Exception] = []
        try:
            packLoader.close()
        except Exception as err:
            errors.append(err)

        try:
            if runtime.applicationRun.active:
                runtime.stop()
            else:
                runtime.abortInitialization()
        except Exception as err:
            errors.append(err)
        return errors
