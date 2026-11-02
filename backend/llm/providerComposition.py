# file: backend/llm/providerComposition.py ; version: 1
"""Trusted activation composition, distinct from execution admission and Jobs."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from threading import RLock
from typing import Protocol

from backend.llm.errors import LlmExecutionOwnershipError, LlmExecutionCleanupError

_invocation = ContextVar("actant.composition.invocation", default=False)
_lifecycle = ContextVar("actant.composition.lifecycle", default=False)


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
    """Reject activation under serialized invocation without changing Job semantics."""
    @wraps(method)
    def execute(self, *args, **kwargs):
        """Keep copied/reentrant invocation scope from releasing enclosing lanes."""
        self.requireCompositionReady()
        token = _invocation.set(True)
        try:
            return method(self, *args, **kwargs)
        finally:
            _invocation.reset(token)
    return execute


class ActivationAttempt:
    """Exact runtime-issued batch owner with callback-free revocation and rollback."""

    def __init__(self, runtime, *, initial=False, mode=None):
        """Reserve one batch before any registration or composer callback is run."""
        if _invocation.get() or _lifecycle.get():
            raise LlmExecutionOwnershipError("Activation cannot run inside an invocation or lifecycle hook.")
        self.runtime, self.initial, self.mode = runtime, initial, mode
        self._gate = RLock()
        self.checkpoint = len(runtime.packLoader._loadedPacks)
        self.previousBarrier = runtime.packLoader._activationBarrierReached
        self.phase = "preparing"
        self.revoked = False
        self.snapshot = None
        with runtime._lane:
            if runtime._compositionAttempt is not None or runtime._closed:
                raise LlmExecutionOwnershipError("Activation is already pending or runtime is closed.")
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
        self.phase = "composing"

    def compose(self):
        """Call trusted composers with no enclosing host/runtime/registry lane."""
        self.requireCurrent()
        for composer in self.runtime.providerComposers:
            composer.compose(self.snapshot)
            self.requireCurrent()
        for registration in self.snapshot.registrations:
            if getattr(registration.value, "requiresControlledComposition", False):
                if self.runtime.llmExecutions.binding(self.snapshot.registry, registration) is None:
                    raise LlmExecutionOwnershipError("Managed llama.cpp requires trusted host composition.")
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
        self.runtime.llmExecutions.finishClose(self.runtime.llmProviders)
        with self.runtime._lane:
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


def requireNoPendingComposition(runtime):
    """Close admission immediately; never unload objects still owned by a composer."""
    attempt = runtime._compositionAttempt
    if attempt is not None and attempt.phase not in ("cleanup", "aborted", "complete"):
        attempt.abandon()
        runtime.llmExecutions.revokeRegistry(runtime.llmProviders, permanent=True)
        raise LlmExecutionCleanupError()
