# file: backend/llm/providerComposition.py ; version: 4
"""Trusted activation composition, distinct from execution admission and Jobs."""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from threading import RLock, get_ident
from typing import Protocol
from weakref import WeakKeyDictionary

from backend.llm.errors import LlmExecutionOwnershipError, LlmExecutionCleanupError

_invocation = ContextVar("actant.composition.invocation", default=False)
_lifecycle = ContextVar("actant.composition.lifecycle", default=False)
_effectTables = WeakKeyDictionary()
_effectTablesLane = RLock()


class _EffectOwnership:
    """Exact service's cleanup callback ownership, not execution/resource admission."""

    def __init__(self):
        """Keep callbacks and a resource disposer mutually exclusive without holding lanes."""
        self.lane = RLock()
        self.callbacks = set()
        self.callbackThread = None
        self.disposer = None


def _effectOwnership(resources):
    """Return the stable effect gate even after a runtime leaves its host's tables."""
    with _effectTablesLane:
        if resources not in _effectTables:
            _effectTables[resources] = _EffectOwnership()
        return _effectTables[resources]


@contextmanager
def effectCleanupScope(resources):
    """Reserve actual broker callback ownership before a resource disposer can win."""
    ownership = _effectOwnership(resources)
    ticket = object()
    with ownership.lane:
        if ownership.disposer is not None or ownership.callbackThread not in (None, get_ident()):
            raise LlmExecutionCleanupError()
        ownership.callbacks.add(ticket)
        ownership.callbackThread = get_ident()
    try:
        yield
    finally:
        with ownership.lane:
            ownership.callbacks.discard(ticket)
            if not ownership.callbacks:
                ownership.callbackThread = None


def finishResourceEffects(resources):
    """Exclusively own core close/effects; callback-free runtime shutdown precedes this.

    Do not close the neutral service before owning this effect boundary: an earlier
    live callback must still be able to perform its own authorized idle unload.
    """
    ownership = _effectOwnership(resources)
    ticket = object()
    with ownership.lane:
        if ownership.callbacks or ownership.disposer is not None:
            raise LlmExecutionCleanupError()
        ownership.disposer = ticket
    try:
        resources.finishClose()
    finally:
        with ownership.lane:
            ownership.disposer = None


def finishBrokerEffects(bindings, resources, runtimes=(), registry=None):
    """Protect explicit preflight/body drains as well as outer lifetime callbacks."""
    with effectCleanupScope(resources):
        with finalizationScope(runtimes) as blocked:
            if blocked:
                raise LlmExecutionCleanupError()
            bindings.finishClose(registry)


class ProviderComposer(Protocol):
    """Host-installed composition; Pack registration cannot install this authority."""

    def compose(self, snapshot: ActivationCompositionSnapshot) -> None:
        """Bind exact published registrations and actual owners before hooks run."""
        ...


@dataclass(frozen=True, slots=True)
class ActivationCompositionSnapshot:
    """Immutable identity snapshot; contains no invocation Context or memory view."""

    resources: object
    bindings: object
    registry: object
    registrations: tuple
    owners: tuple
    ticket: object


def invocationBoundary(method):
    """Reserve lifecycle exclusion through actual invocation and outside-lane cleanup."""
    @wraps(method)
    def execute(self, *args, **kwargs):
        """Keep copied/reentrant invocation scope from releasing enclosing lanes."""
        self.requireCompositionReady()
        admission = reserveInvocation(self)
        token = _invocation.set((self, admission))
        try:
            return method(self, *args, **kwargs)
        finally:
            _invocation.reset(token)
            with self._admissionLane:
                self._invocationAdmissions.pop(admission, None)
    return execute


def requireReady(runtime):
    """Check readiness for diagnostics; actual invocation also reserves admission."""
    with runtime._admissionLane:
        _requireReadyUnlocked(runtime)


def _requireReadyUnlocked(runtime):
    """Inspect only runtime-issued gate state; never call user code under this lane."""
    runtime.processRunner.requireReady()
    if runtime._closed or runtime._shutdownAdmission or runtime._unloadAdmission:
        raise LlmExecutionOwnershipError("Runtime execution admission is closed.")
    attempt = runtime._compositionAttempt
    if attempt is not None and (attempt.revoked or attempt.phase != "running"
            or _lifecycle.get() is not attempt or attempt.continuationThread != get_ident()):
        raise LlmExecutionOwnershipError("Application activation has not completed provider composition.")


def reserveInvocation(runtime):
    """Atomically choose invocation versus activation before waiting for execution lane."""
    with runtime._admissionLane:
        _requireReadyUnlocked(runtime)
        admission = object()
        runtime._invocationAdmissions[admission] = get_ident()
        return admission


def requireInvocationEntry(runtime):
    """Validate the issued live admission again at the actual serialized use boundary."""
    current = _invocation.get()
    with runtime._admissionLane:
        if (not current or current[0] is not runtime
                or runtime._invocationAdmissions.get(current[1]) != get_ident()):
            raise LlmExecutionOwnershipError("Invocation admission is foreign, copied or expired.")
        _requireReadyUnlocked(runtime)


def hasLifecycleWork(runtime):
    """Shutdown cannot enter legacy teardown while invocation/finalization still owns work."""
    with runtime._admissionLane:
        return bool(runtime._invocationAdmissions or runtime._cleanupAdmissions)


@contextmanager
def finalizationScope(runtimes):
    """Atomically reserve outside-lock finalizers against a new activation attempt."""
    issued = []
    blocked = set()
    try:
        for runtime in dict.fromkeys(runtimes):
            ownership = _effectOwnership(runtime.executionResources)
            with ownership.lane, runtime._admissionLane:
                attempt = runtime._compositionAttempt
                if (ownership.disposer is not None or ownership.callbackThread not in (None, get_ident()) or (attempt is not None
                        and attempt.phase not in ("cleanup", "aborted", "complete"))):
                    blocked.add(runtime)
                else:
                    admission = object()
                    runtime._cleanupAdmissions.add(admission)
                    ownership.callbacks.add(admission)
                    ownership.callbackThread = get_ident()
                    issued.append((runtime, admission, ownership))
        yield blocked
    finally:
        for runtime, admission, ownership in issued:
            with ownership.lane, runtime._admissionLane:
                runtime._cleanupAdmissions.discard(admission)
                ownership.callbacks.discard(admission)
                if not ownership.callbacks:
                    ownership.callbackThread = None


def packUnloadBoundary(method):
    """Keep a reversible unload admission closed until outside-lock cleanup finishes."""
    @wraps(method)
    def execute(self, *args, **kwargs):
        """A surrounding permanent runtime shutdown already owns its closed gate."""
        runtime = self._runtime
        if runtime._llmClosePrepared and runtime._shutdownAdmission:
            return method(self, *args, **kwargs)
        runtime.prepareLlmClose(permanent=False)
        result = method(self, *args, **kwargs)
        with runtime._admissionLane:
            runtime._unloadAdmission = False
            runtime._llmClosePrepared = False
        return result
    return execute


class ActivationAttempt:
    """Exact runtime-issued batch owner with callback-free revocation and rollback."""

    def __init__(self, runtime, *, initial=False, mode=None):
        """Reserve one batch before any registration or composer callback is run."""
        if _invocation.get() or _lifecycle.get():
            raise LlmExecutionOwnershipError("Activation cannot run inside an invocation or lifecycle hook.")
        self.runtime, self.initial, self.mode = runtime, initial, mode
        self._gate = runtime._admissionLane
        self.phase = "preparing"
        self.revoked = False
        self.snapshot = None
        self.continuationThread = None
        with self._gate:
            runtime.processRunner.requireReady()
            if (runtime._compositionAttempt is not None or runtime._closed or runtime._shutdownAdmission
                    or runtime._unloadAdmission or runtime._invocationAdmissions or runtime._cleanupAdmissions):
                raise LlmExecutionOwnershipError("Activation is busy or runtime admission is closed.")
            self.checkpoint = len(runtime.packLoader._loadedPacks)
            self.previousBarrier = runtime.packLoader._activationBarrierReached
            runtime._compositionAttempt = self

    def requireCurrent(self):
        """A late callback cannot revive an abandoned activation or owner set."""
        with self._gate:
            if self.runtime._compositionAttempt is not self or self.revoked or self.runtime._closed:
                raise LlmExecutionOwnershipError("Activation attempt is revoked or stale.")

    def seal(self):
        """Capture exact published registrations/owners while staging is serialized."""
        self.requireCurrent()
        runtime = self.runtime
        with runtime.llmProviders.executionPublication() as registrations:
            self.snapshot = ActivationCompositionSnapshot(runtime.executionResources,
                runtime.llmExecutions, runtime.llmProviders, registrations,
                tuple((identity, runtime._llmOwners[key]) for key, (identity, _) in runtime._codeEntries.items()), self)
        with self._gate:
            self.requireCurrent()
            self.phase = "composing"

    def compose(self):
        """Call trusted composers with no enclosing host/runtime/registry lane."""
        self.requireCurrent()
        for composer in self.runtime.providerComposers:
            composer.compose(self.snapshot)
            self.requireCurrent()
        for registration in self.snapshot.registrations:
            if getattr(registration.value, "requiresControlledComposition", False):
                binding = self.runtime.llmExecutions.binding(self.snapshot.registry, registration)
                if binding is None:
                    raise LlmExecutionOwnershipError("Managed llama.cpp requires trusted host composition.")
                binding.requireLive()
        with self._gate:
            self.requireCurrent()
            self.phase = "ready"

    def complete(self):
        """End only this issued batch after successful composition/continuation."""
        with self.runtime._lane:
            with self._gate:
                self.requireCurrent()
                self.phase = "complete"
                self.runtime._compositionAttempt = None

    def abandon(self):
        """Close batch admission without running any provider cleanup callbacks."""
        with self._gate:
            self.revoked = True
            runtime = self.runtime
            if self.initial:
                runtime._shutdownAdmission = True
        if self.initial:
            runtime.llmExecutions.revokeRegistry(runtime.llmProviders, permanent=True)
        else:
            for loaded in runtime.packLoader._loadedPacks[self.checkpoint:]:
                for entry in loaded.entries:
                    runtime.revokeLlmCodeEntry(entry.identity.codeEntryInstanceId)

    def abort(self):
        """Unwind only this batch; report every cleanup failure without losing roots."""
        self.abandon()
        runtime = self.runtime
        errors = []
        with runtime._lane:
            with self._gate:
                self.phase = "cleanup"
            loader = runtime.packLoader
            errors.extend(loader._closeLoadedPacks(loader._loadedPacks[self.checkpoint:]))
            del loader._loadedPacks[self.checkpoint:]
            loader._activationBarrierReached = self.previousBarrier
            if self.initial:
                runtime.abortInitialization()
        try:
            self.finishCleanup()
        except Exception as error:
            errors.append(error)
        if errors:
            raise ExceptionGroup("Activation rollback reported cleanup errors.", errors)

    def finishCleanup(self):
        """Drain broker-owned callbacks outside lanes, retaining incomplete attempts."""
        with finalizationScope((self.runtime,)) as composing:
            if composing:
                raise LlmExecutionCleanupError()
            self.runtime.llmExecutions.finishClose(self.runtime.llmProviders)
            with self.runtime._lane:
                with self._gate:
                    self.phase = "aborted"
                    if self.runtime._compositionAttempt is self:
                        self.runtime._compositionAttempt = None


def activateBatch(loader, action):
    """Compose a direct activation before publishing its successful barrier."""
    runtime = loader._runtime
    attempt = ActivationAttempt(runtime)
    try:
        with runtime._lane:
            action()
            attempt.seal()
        attempt.compose()
        attempt.complete()
    except BaseException as primary:
        try:
            attempt.abort()
        except Exception as cleanup:
            primary.llmCompositionCleanup = cleanup
        raise


def requireNoPendingComposition(runtime, *, permanent=True):
    """Reserve shutdown atomically before any finalizer; a preflight is not authority.

    Permanent runtime/host shutdown never reopens admission. Pack unloading keeps a
    reversible gate until its whole outer cleanup boundary succeeds. Callbacks and
    cooperative stops run after this callback-free gate/registry revocation stage.
    """
    with runtime._admissionLane:
        if permanent:
            runtime._shutdownAdmission = True
        else:
            runtime._unloadAdmission = True
        attempt = runtime._compositionAttempt
        pending = attempt is not None and attempt.phase not in ("cleanup", "aborted", "complete")
        if pending:
            attempt.revoked = True
    runtime.llmExecutions.revokeRegistry(runtime.llmProviders, permanent=permanent)
    if pending:
        attempt.abandon()
        raise LlmExecutionCleanupError()
