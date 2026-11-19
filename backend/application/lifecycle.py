# file: backend/application/lifecycle.py ; version: 8
from __future__ import annotations

from backend.packs.runtime import ManualActivationPlan
from backend.application.applicationRuntime import ApplicationRuntime
from backend.save import SaveBundle
from backend.llm.providerComposition import ActivationAttempt, _lifecycle
from threading import get_ident

__all__ = ["ApplicationLifecycle"]


class ApplicationLifecycle:
    """Coordinates Pack activation, Application hooks, and persistence order.

    Storage representation remains ApplicationRuntime/ApplicationStore responsibility.
    ApplicationRuntime owns its PackLoader, which performs mechanical CodeEntry
    hook invocation. This coordinator
    supplies the semantic ordering between those boundaries.
    """

    @classmethod
    def create(
        cls,
        *,
        runtime: ApplicationRuntime,
        plan: ManualActivationPlan,
    ) -> SaveBundle:
        """Creates and publishes a new persistent Application, then starts its run."""
        if runtime.acceptedSaveBundle is not None:
            raise RuntimeError("Application creation requires an ApplicationRuntime with no accepted SaveBundle.")

        return cls._execute(runtime=runtime, plan=plan, mode="create")

    @classmethod
    def _resumeCreate(cls, *, runtime):
        """Preserve create/load/run and authoritative save ordering after composition."""
        root = runtime.applicationRun.application.committedState
        creationTransaction = root.openTransaction()
        try:
            runtime.packLoader.invokeApplicationCreate(memoryView=creationTransaction)
            creationTransaction.commit()
        except Exception:
            try:
                creationTransaction.abort()
            except RuntimeError:
                pass
            raise

        accepted = runtime.saveApplication()

        beforeLoad = root.snapshot()
        runtime.packLoader.invokeApplicationLoad()
        if root.snapshot() != beforeLoad:
            accepted = runtime.saveApplication()

        runtime.start()
        runtime.packLoader.invokeApplicationRun()
        return accepted

    @classmethod
    def load(
        cls,
        *,
        runtime: ApplicationRuntime,
        plan: ManualActivationPlan,
    ) -> SaveBundle:
        """Runs loaded-Application lifecycle and starts a fresh ApplicationRun."""
        accepted = runtime.acceptedSaveBundle
        if accepted is None:
            raise RuntimeError("Application loading requires an ApplicationRuntime restored from an accepted SaveBundle.")

        return cls._execute(runtime=runtime, plan=plan, mode="load")

    @classmethod
    def _resumeLoad(cls, *, runtime):
        """Preserve create/load/run and authoritative save ordering after composition."""
        accepted = runtime.acceptedSaveBundle
        root = runtime.applicationRun.application.committedState
        beforeLoad = root.snapshot()
        runtime.packLoader.invokeApplicationLoad()
        if root.snapshot() != beforeLoad:
            accepted = runtime.saveApplication()

        runtime.start()
        runtime.packLoader.invokeApplicationRun()
        return accepted

    @classmethod
    def prepare(cls, *, runtime, plan, mode):
        """Stage the complete activation before hooks, commits, save or start."""
        attempt = ActivationAttempt(runtime, initial=True, mode=mode)
        runtime.beginInitialization()
        runtime.packLoader._activatePlan(plan)
        attempt.seal()
        return attempt

    @classmethod
    def resume(cls, attempt):
        """Only a live issued continuation can enter serialized application hooks."""
        with attempt._gate:
            attempt.requireCurrent()
            if attempt.phase != "ready":
                raise RuntimeError("Provider composition is not ready.")
            attempt.phase = "running"
            attempt.continuationThread = get_ident()
        token = _lifecycle.set(attempt)
        try:
            result = (cls._resumeCreate(runtime=attempt.runtime) if attempt.mode == "create"
                      else cls._resumeLoad(runtime=attempt.runtime))
            attempt.complete()
            return result
        finally:
            _lifecycle.reset(token)

    @classmethod
    def abort(cls, runtime, primary):
        """Unwind partial initialization outside host lanes and preserve failures."""
        errors = []
        attempt = runtime._compositionAttempt
        if attempt is not None:
            try:
                attempt.abort()
            except Exception as error:
                errors.append(error)
        if runtime._compositionAttempt is None:
            errors.extend(cls._cleanupFailedStart(runtime=runtime))
        if errors:
            primary.llmCompositionCleanup = tuple(errors)
            raise ExceptionGroup("Initialization and cleanup failed.", [primary, *errors]) from None

    @classmethod
    def _execute(cls, *, runtime, plan, mode):
        """Standalone create/load uses the identical pre-hook composition barrier."""
        try:
            with runtime._lane:
                attempt = cls.prepare(runtime=runtime, plan=plan, mode=mode)
            attempt.compose()
            with runtime._lane:
                return cls.resume(attempt)
        except Exception as primary:
            cls.abort(runtime, primary)
            raise

    @staticmethod
    def _cleanupFailedStart(
        *,
        runtime: ApplicationRuntime,
    ) -> list[Exception]:
        """Close failed initialization after composition rollback, preserving errors."""
        try:
            runtime.close()
        except Exception as err:
            return [err]
        return []
