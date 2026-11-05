# file: backend/llm/executionSessions.py ; version: 6
"""Trusted LLM composition over ER-01, with no provider-specific realization.

Binding/owner creation and adapter evidence are backend hooks, never Pack grants.
Public handles retain no invocation Context, transaction or memory/IO view. Normal
provider calls preserve runtime serialization; queued lifetime cleanup is drained
outside runtime/host locks. Unknown evidence remains blocked by ER-01.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import wraps
from inspect import signature
from threading import RLock
from typing import Protocol
from weakref import WeakSet

from backend.llm.errors import (
    LlmContextLostError, LlmExecutionCleanupError, LlmExecutionConflictError,
    LlmExecutionOwnershipError, LlmProviderProtocolError, LlmStalePreviewError,
    LlmUnsupportedExecutionError,
)
from backend.llm.llmTypes import LlmExecutionProfile
from backend.orchestration.cancellation import ExecutionCancelled
from backend.runtime.executionResources import (
    EffectStatus, ProviderEffect, Quiescence, ResourceConflict, ResourceState, ResourceCleanupError,
)

_OK = ProviderEffect(EffectStatus.ACKNOWLEDGED, Quiescence.CONFIRMED)
_UNKNOWN = ProviderEffect(EffectStatus.FAILED, Quiescence.UNKNOWN)


@dataclass(frozen=True, slots=True)
class FreshContext:
    """Requests owned logical freshness, independently of weights and capacity.

    required concerns only the installed adapter's owned logical-context contract,
    not all hidden caches, GPU reset or deterministic results. Retention is always an
    explicit requirement; unsupported retention never silently becomes fresh-only.
    """

    retain: bool = False
    required: bool = False

    def __post_init__(self):
        """Rejects malformed request flags; these flags are requests, not grants."""
        if type(self.retain) is not bool or type(self.required) is not bool:
            raise TypeError("Fresh context request flags must be bool.")


@dataclass(frozen=True, slots=True)
class ReuseContext:
    """Requires the exact issued retained state; no replay/rebuild fallback."""

    handle: LlmRetainedContextHandle = field(repr=False, compare=False)


class ContextState(StrEnum):
    """Narrow trusted context disposition, distinct from operation quiescence."""

    NONE = "none"
    RETAINED = "retained"
    LOST = "lost"


class LlmSessionExit(StrEnum):
    """Why a trusted call ticket reaches its exactly-once adapter finalizer."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LlmProviderExecutionSession(Protocol):
    """Generation-bound trusted provider operations inside one admitted ticket."""

    def getExecutionProfile(self) -> LlmExecutionProfile:
        """Resolves constraints and a scoped estimator without changing residency."""
        ...

    def prepareContext(self, query, request) -> LlmContextEffect:
        """Performs the concrete owned fresh/reuse action before streaming."""
        ...

    def stream(self, request):
        """Returns the provider stream tied to this exact generation/context."""
        ...


class LlmExecutionAdapter(Protocol):
    """Full installed trusted contract; optional-method discovery is not authority."""

    supportsFresh: bool
    supportsRequiredFresh: bool
    supportsRetention: bool

    def openExecution(self, admission, model, options) -> LlmProviderExecutionSession:
        """Opens one exact ticket, whose cleanup is owned even if this hook raises."""
        ...

    def closeExecution(self, admission, session, reason) -> LlmSessionCleanup:
        """Finalizes that ticket exactly once with truthful quiescence/context evidence."""
        ...


@dataclass(frozen=True, slots=True)
class LlmProviderContext:
    """Trusted transfer of one owned resource and concrete deferred disposer."""

    payload: object = field(repr=False, compare=False)
    disposer: Callable[[object], ProviderEffect] = field(repr=False, compare=False)

    def __post_init__(self):
        """Requires cleanup ownership before accepting a provider object."""
        if not callable(self.disposer):
            raise TypeError("Retained context requires a disposer.")


@dataclass(frozen=True, slots=True)
class LlmSessionCleanup:
    """Adapter-only execution evidence and context disposition for an exact ticket."""

    effect: ProviderEffect
    contextState: ContextState = ContextState.NONE
    retained: LlmProviderContext | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        """Validates bounded records without touching provider payloads."""
        if type(self.effect) is not ProviderEffect or type(self.contextState) is not ContextState:
            raise TypeError("Invalid session cleanup evidence.")
        if self.retained is not None and type(self.retained) is not LlmProviderContext:
            raise TypeError("Invalid retained context transfer.")


@dataclass(frozen=True, slots=True)
class LlmContextEffect:
    """Evidence for owned logical context preparation, never global cache claims."""

    status: EffectStatus
    ownedLogicalContext: bool = True

    def __post_init__(self):
        """Accepts only the concrete scope supported by this minimal contract."""
        if type(self.status) is not EffectStatus or self.ownedLogicalContext is not True:
            raise TypeError("Expected owned logical-context evidence.")


@dataclass(frozen=True, slots=True)
class LlmExecutionEvidence:
    """Safe immutable result evidence, excluding authority, estimators and payloads."""

    residency: object
    contextEffect: LlmContextEffect | None
    cleanup: ProviderEffect
    iteratorCloseFailed: bool = False

    def snapshot(self):
        """Exports immutable diagnostic facts, never live handles or domain bearer tokens."""
        return {"residencyGeneration": self.residency.generation,
                "residencyIdentity": self.residency.identity.hex(),
                "contextScope": "owned-logical-context" if self.contextEffect else None,
                "contextEffect": self.contextEffect.status.value if self.contextEffect else None,
                "cleanupStatus": self.cleanup.status.value, "quiescence": self.cleanup.quiescence.value,
                "iteratorCloseFailed": self.iteratorCloseFailed}


@dataclass(frozen=True, slots=True)
class LlmOwnerGrant:
    """Trusted composition grant of selectors and optional idle-transition authority."""

    selectors: tuple[str | None, ...]
    allowTransition: bool = False

    def __post_init__(self):
        """Requires immutable builtin selectors before any broker publication lane."""
        if type(self.selectors) is not tuple or type(self.allowTransition) is not bool:
            raise TypeError("Host grants require tuple selectors and bool transition authority.")
        if any(s is not None and (type(s) is not str or not s) for s in self.selectors):
            raise ValueError("Host grant selectors must be exact strings or None.")


class LlmExecutionOwner:
    """Exact runtime-issued owner shared by fresh invocations of one CodeEntry."""

    def __init__(self, service, registry):
        """Records only the authority issuer/registry, never a Context or transaction."""
        self._service, self._registry = service, registry
        self._closed = False

    def invocation(self, cancellationSignal=None):
        """Trusted runtime creates short-lived access for a normal capability call."""
        return self._service.invocation(self, cancellationSignal)

    def revoke(self):
        """Marks this owner closed without callbacks, even under legacy teardown locks."""
        self._service.revokeOwner(self)


class LlmInvocationAccess:
    """Independent validity token; retaining it never retains CodeEntryContext."""

    def __init__(self, owner, cancellationSignal):
        """Creates an unissued token; service membership establishes actual authority."""
        self.owner, self.cancellationSignal = owner, cancellationSignal
        self._valid = True

    def requireValid(self):
        """Rejects new use after invocation, owner or host admission revocation."""
        service = self.owner._service
        with service._lane:
            valid = self in service._accesses and self._valid and not self.owner._closed
        if not valid:
            raise LlmExecutionOwnershipError("LLM invocation access is closed or foreign.")
        if self.cancellationSignal is not None:
            self.cancellationSignal.raiseIfRequested()

    def close(self):
        """Revokes invocation use without revoking cleanup or run-owned handles."""
        with self.owner._service._lane:
            self._valid = False
            service = self.owner._service
            if any(c.access is self and not c._closed for b in service._bindings.values() for c in b.calls):
                service._pendingAccesses.add(self)


class LlmProfileReference:
    """Opaque owner-scoped selector issued from an installed controlled binding."""

    def __init__(self, binding, owner, selector):
        """Binds exact registered authority; no provider payload is exposed."""
        self._binding, self._owner, self._selector = binding, owner, selector


class LlmConversationSession:
    """Run/CodeEntry-owned exclusive reservation with no independent compute API."""

    def __init__(self, profile, reservation):
        """Wraps the existing ER-01 reservation rather than duplicating exclusion."""
        self._profile, self._reservation = profile, reservation
        self._closed = False

    def close(self):
        """Queues cleanup-only revocation; no evidence can be supplied by the owner."""
        service = self._profile._binding.service
        with service._lane:
            if type(self) is not LlmConversationSession or self not in service._conversations:
                raise LlmExecutionOwnershipError("Conversation is foreign or unissued.")
            self._closed = True
            service._pendingConversations.add(self)
            for handle in self._profile._binding.handles:
                if handle._conversation is self:
                    service._invalidateUnlocked(handle)


class LlmRetainedContextHandle:
    """Owner-scoped derived-state handle backed by an ER-01 residency/context pin."""

    def __init__(self, call, resource, pin):
        """Binds private adapter state without keeping any invocation access alive."""
        self._binding, self._owner = call.binding, call.access.owner
        self._profile, self._pin, self._resource = call.profileReference, pin, resource
        self._conversation = call.conversation
        self._lost = False
        self._revision = 0
        self._provisional = None
        self._disposed = False

    @property
    def residency(self):
        """Returns immutable residency evidence without granting provider authority."""
        return self._pin.token

    @property
    def contextGeneration(self):
        """Returns the separate immutable generation allocated by ER-01."""
        return self._pin.contextGeneration

    def close(self):
        """Revokes and queues disposal; never accepts trusted cleanup assertions."""
        self._binding.service.invalidate(self)

    def invalidate(self):
        """Trusted loss notification targets this exact handle and context generation."""
        self.close()


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class LlmPreviewBinding:
    """Opaque preview provenance; it is neither a persistent pin nor authority."""

    profile: LlmProfileReference = field(repr=False, compare=False)
    registration: object = field(repr=False, compare=False)
    residency: object
    options: object = field(repr=False, compare=False)


class LlmMutationScope:
    """Narrow provisional-use guard and revision receipts, not a transaction engine."""

    def __init__(self, parent=None):
        """Tracks only handles/revisions, never memory, IO or transaction objects."""
        self.parent = parent
        self.root = self if parent is None else parent.root
        self.receipts = []
        self.active = True

    def touch(self, handle):
        """Marks mutation only after admission; provisional handles exclude other roots."""
        service = handle._binding.service
        with service._lane:
            if not self.active or not self.root.active:
                raise LlmExecutionOwnershipError("LLM mutation receipt scope is expired.")
            handle._revision += 1
            handle._provisional = self.root
            self.receipts.append((handle, handle._revision))

    def resolve(self, accepted):
        """Exact receipts prevent old aborts from invalidating a later handle mutation."""
        if not self.active:
            return
        self.active = False
        if accepted and self.parent is not None:
            self.parent.receipts.extend(self.receipts)
            self.receipts.clear()
            return
        for handle, revision in self.receipts:
            service = handle._binding.service
            with service._lane:
                if handle._revision != revision:
                    continue
                if not accepted:
                    service._invalidateUnlocked(handle)
                elif handle._provisional is self.root:
                    handle._provisional = None
        self.receipts.clear()


_mutation = ContextVar("actant.llm.mutation", default=None)
_boundary = ContextVar("actant.llm.call-boundary", default=None)
_boundaryCancellation = ContextVar("actant.llm.call-cancellation", default=None)
_helper = ContextVar("actant.llm.helper-estimation", default=None)
_lifetime = ContextVar("actant.llm.lifetime-boundary", default=None)


def lifetimeBoundary(method):
    """Defer callbacks outside lanes and reserve actual finalization versus activation."""
    @wraps(method)
    def execute(self, *args, **kwargs):
        """Root cleanup and admission tickets through failure, removal and repeated close."""
        from backend.llm.providerComposition import finalizationScope, effectCleanupScope, finishResourceEffects
        outer = _lifetime.get()
        if outer is not None and not outer[0]:
            raise LlmExecutionOwnershipError("LLM lifetime scope is expired.")
        scope = outer if outer is not None else [True, [], []]
        token = _lifetime.set(scope)
        runtime = getattr(self, "_runtime", self)
        registry = getattr(runtime, "llmProviders", None)
        if registry is not None:
            scope[1].append((registry.executions, registry, runtime.executionResources))
            scope[2].append(runtime)
        elif hasattr(self, "llmExecutions"):
            scope[1].append((self.llmExecutions, None, self.executionResources))
            scope[2].extend(tuple(getattr(self, "_runtimesByRunId", {}).values()))
            scope[2].extend(tuple(getattr(self, "_pendingInitializations", {}).values()))
        primary = None
        result = None
        try:
            result = method(self, *args, **kwargs)
            return result
        except BaseException as error:
            primary = error
            raise
        finally:
            _lifetime.reset(token)
            if outer is None:
                scope[0] = False
                scope[2].extend(tuple(getattr(self, "_runtimesByRunId", {}).values()))
                scope[2].extend(tuple(getattr(self, "_pendingInitializations", {}).values()))
                errors = []
                if (getattr(runtime, "_closed", False) and getattr(runtime, "_ownsExecutionResources", False)):
                    try:
                        finishResourceEffects(runtime.executionResources)
                    except Exception as error:
                        errors.append(error)
                with finalizationScope(scope[2]) as composing:
                    work = []
                    blockedRegistries = {id(r.llmProviders) for r in composing}
                    for service, target, resources in scope[1]:
                        if target is None and composing:
                            work.extend((service, r.llmProviders, r.executionResources) for r in dict.fromkeys(scope[2])
                                if r not in composing and r.llmExecutions is service)
                        elif target is None or id(target) not in blockedRegistries:
                            work.append((service, target, resources))
                    completeServices = {id(service) for service, target, resources in work if target is None}
                    seen = set()
                    for service, target, resources in work:
                        if target is not None and id(service) in completeServices:
                            continue
                        key = (id(service), id(target))
                        if key in seen:
                            continue
                        seen.add(key)
                        try:
                            with effectCleanupScope(resources):
                                service.finishClose(target)
                        except Exception as error:
                            errors.append(error)
                if errors:
                    self.lastLlmCleanup = tuple(errors)
                    if primary is not None:
                        primary.llmLifetimeCleanup = tuple(errors)
                    elif getattr(result, "state", None) not in ("Failed", "Cancelled"):
                        raise ExceptionGroup("LLM lifetime cleanup is incomplete.", errors)
    return execute


def jobExecutionBoundary(method):
    """Tracks context receipts for this actual Job mutation, including swallowed errors."""
    @wraps(method)
    def execute(*args, **kwargs):
        """Keeps independent nested Jobs from borrowing an uncommitted parent context."""
        scope = LlmMutationScope()
        token = _mutation.set(scope)
        try:
            result = method(*args, **kwargs)
        except BaseException:
            scope.resolve(False)
            raise
        else:
            scope.resolve(result.authoritativeStateAccepted)
            return result
        finally:
            _mutation.reset(token)
    return execute


def hostClosePreflight(method):
    """Stops new controlled work outside host lanes before legacy runtime teardown."""
    callSignature = signature(method)
    @wraps(method)
    def execute(host, *args, **kwargs):
        """Returns pending immediately instead of waiting across runtime mutation lanes."""
        bound = callSignature.bind(host, *args, **kwargs)
        if method.__name__ == "stop":
            with host._lane:
                host._stopping = True
                from backend.llm.providerComposition import (requireNoPendingComposition, hasLifecycleWork,
                    finishResourceEffects, finishBrokerEffects)
                runtimes = tuple(dict.fromkeys((*host._pendingInitializations.values(), *host._runtimesByRunId.values())))
                for runtime in runtimes:
                    try:
                        requireNoPendingComposition(runtime)
                    except LlmExecutionCleanupError:
                        pass  # Admission is closed; callback ownership is checked before effects below.
                pending = tuple(r for r in runtimes if r._compositionAttempt is not None
                    or r.applicationRun.applicationRunId in host._pendingInitializations)
                host.llmExecutions.revokeAll()
            if pending:
                if any(runtime._compositionAttempt is not None and runtime._compositionAttempt.phase
                       not in ("cleanup", "aborted", "complete") for runtime in pending):
                    raise LlmExecutionCleanupError()
                try:
                    finishResourceEffects(host.executionResources)
                except ResourceCleanupError:
                    pass  # Owned unknown disposition is reported by the final shutdown pass.
                for runtime in pending:
                    attempt = runtime._compositionAttempt
                    if attempt is not None and attempt.phase == "cleanup":
                        with _ignoreIncompleteCleanup():
                            attempt.finishCleanup()
                    if runtime._compositionAttempt is None:
                        runtime.close(_deferLlmClose=True)
                        from backend.runtime.sharedServices import unbindApplicationRunSharedServices
                        unbindApplicationRunSharedServices(runtime.applicationRun.applicationRunId)
                        with host._lane:
                            host._pendingInitializations.pop(runtime.applicationRun.applicationRunId, None)
                            host._initializingApplicationIds.discard(runtime.applicationRun.application.applicationId)
                if host._pendingInitializations:
                    raise LlmExecutionCleanupError()
            with _ignoreIncompleteCleanup():
                finishBrokerEffects(host.llmExecutions, host.executionResources, runtimes)
            try:
                finishResourceEffects(host.executionResources)
            except ResourceCleanupError:
                pass
            with _ignoreIncompleteCleanup():
                finishBrokerEffects(host.llmExecutions, host.executionResources, runtimes)
            if host.llmExecutions.pendingCalls() or any(hasLifecycleWork(r) for r in runtimes):
                raise LlmExecutionCleanupError()
        else:
            applicationRunId = bound.arguments["applicationRunId"]
            if type(applicationRunId) is not str or not applicationRunId:
                raise ValueError("applicationRunId must be a non-empty string.")
            with host._lane:
                host.requireActive()
                runtime = host._runtimesByRunId[applicationRunId]
                host.llmExecutions.revokeRegistry(runtime.llmProviders)
            from backend.llm.providerComposition import (requireNoPendingComposition, hasLifecycleWork,
                    finishBrokerEffects)
            requireNoPendingComposition(runtime)
            with _ignoreIncompleteCleanup():
                finishBrokerEffects(host.llmExecutions, host.executionResources, (runtime,), runtime.llmProviders)
            if host.llmExecutions.pendingCalls(runtime.llmProviders) or hasLifecycleWork(runtime):
                raise LlmExecutionCleanupError()
        return method(host, *args, **kwargs)
    return execute


@contextmanager
def _ignoreIncompleteCleanup():
    """Allows phased cleanup to advance before its final retained disposition is reported."""
    try:
        yield
    except (LlmExecutionCleanupError, ResourceCleanupError, ExceptionGroup):
        pass


def currentMutationScope():
    """Rejects expired copied scopes rather than treating them as new authority."""
    scope = _mutation.get()
    if scope is not None and (not scope.active or not scope.root.active):
        raise LlmExecutionOwnershipError("LLM mutation scope is expired.")
    return scope


@contextmanager
def mutationScope():
    """Finally-resets nested/copied disposition scopes around pipeline or Job work."""
    scope = LlmMutationScope(currentMutationScope())
    token = _mutation.set(scope)
    try:
        yield scope
    except BaseException:
        scope.resolve(False)
        raise
    else:
        scope.resolve(True)
    finally:
        _mutation.reset(token)


def executionBoundary(method):
    """Ensures every admitted call, including partial open, has one finalizer."""
    @wraps(method)
    def execute(*args, **kwargs):
        """Owns cleanup without changing primary cancellation/error classification."""
        access = kwargs.get("executionAccess")
        if access is not None:
            if type(access) is not LlmInvocationAccess:
                raise LlmExecutionOwnershipError("Expected issued invocation access.")
            access.requireValid()
            signal = kwargs.get("cancellationSignal")
            if signal is not None and signal is not access.cancellationSignal:
                raise LlmExecutionOwnershipError("Cancellation signal does not match invocation authority.")
            kwargs["cancellationSignal"] = access.cancellationSignal
        calls = []
        token = _boundary.set(calls)
        signalToken = _boundaryCancellation.set(kwargs.get("cancellationSignal"))
        try:
            with mutationScope():
                try:
                    result = method(*args, **kwargs)
                except BaseException as error:
                    for call in reversed(calls):
                        call.close(error)
                    raise
                else:
                    for call in reversed(calls):
                        call.close()
                    return result
        finally:
            _boundaryCancellation.reset(signalToken)
            _boundary.reset(token)
    return execute


class _HelperScope:
    """Live nonce plus exact call/signal; copied ContextVars share revocation."""

    def __init__(self, call, signal):
        """Contains no Context and conveys read-only estimation borrowing only."""
        self.call, self.signal, self.active = call, signal, True


@contextmanager
def helperScope(signal):
    """Borrows only the enclosing call during actual build/filter/query callbacks."""
    calls = _boundary.get()
    helper = _HelperScope(calls[-1] if calls and not calls[-1]._closed else None, signal)
    token = _helper.set(helper)
    try:
        yield
    finally:
        helper.active = False
        _helper.reset(token)


def helperCancellation():
    """Runtime capability bridge forwards the live parent's actual Job signal."""
    helper = _helper.get()
    if helper is not None:
        if not helper.active:
            raise LlmExecutionOwnershipError("LLM helper scope is expired.")
        return helper.signal
    return None


def pipelineCancellation():
    """Supplies parent cancellation even when the legacy provider has no admitted call."""
    return _boundaryCancellation.get()


class LlmAdapterAdmission:
    """Trusted adapter ticket and stop hook, excluding finish/owner assertions."""

    def __init__(self, claim, contextResource=None, *, owner=None):
        """Retains only ER-01 cleanup ownership and optional private provider context."""
        self.residency = claim.token
        self.contextResource = contextResource
        self._claim = claim
        self._owner = owner
        self._stopRunning = False

    @property
    def callerIdentity(self):
        """Trusted adapter-only identity comparison; never a diagnostic or grant."""
        return self._owner

    def bindStop(self, callback):
        """Registers an exactly-once cooperative stop through the existing claim."""
        if not callable(callback):
            raise TypeError("Stop callback must be callable.")

        def stop():
            """Tracks adapter lifetime through hook exit; ER-01 still owns all claims."""
            self._stopRunning = True
            try:
                callback()
            finally:
                self._stopRunning = False

        return _require(self._claim.bindStop(stop))


def _require(value):
    """Translates neutral immediate conflicts without retry or provider details."""
    if isinstance(value, ResourceConflict):
        raise LlmExecutionConflictError(value)
    return value


class _Estimator:
    """Guards a provider estimator with the admitted operation's live generation."""

    def __init__(self, call, estimator):
        """Keeps no invocation Context; validity is checked for each use."""
        self.call, self.estimator = call, estimator

    def estimateInputTokens(self, query):
        """Rejects stale use before and after provider IO and validates token counts."""
        self.call.requireValid()
        measured = self.estimator.estimateInputTokens(query)
        self.call.requireValid()
        if type(measured) is not int or measured < 0:
            raise LlmProviderProtocolError("Provider estimator returned an invalid token count.")
        return measured


class LlmCallSession:
    """One scoped provider session protected by exactly one accepted ER-01 claim."""

    def __init__(self, binding, access, profile, claim, options, model, context, retained, conversation=None):
        """Installs cleanup ownership before the first adapter hook may fail."""
        self.binding, self.access, self.profileReference = binding, access, profile
        self.claim, self.options, self.model, self.context = claim, options, model, context
        self.retained = retained
        self.conversation = conversation
        self.admission = LlmAdapterAdmission(claim, retained._resource if retained else None, owner=access.owner)
        self._provider = None
        self._iterator = None
        self._closed = False
        self._profile = None
        self._contextEffect = None
        self._cleanup = None
        self._opened = False
        self._unclaimed = None
        self._pendingReason = None
        self._iteratorCloseFailed = False
        self.retainedContext = retained
        self.scope = currentMutationScope()

    def requireValid(self):
        """Checks live access, exact registration and the still-usable ER-01 claim."""
        self.access.requireValid()
        self.binding.requireLive()
        if self.scope is not None and (not self.scope.active or not self.scope.root.active):
            raise LlmExecutionOwnershipError("LLM execution mutation scope is expired.")
        if self._closed or not self.claim.usable:
            raise LlmExecutionOwnershipError("LLM execution session is closed or unavailable.")
        if self.conversation is not None and self.conversation._closed:
            raise LlmExecutionOwnershipError("Conversation admission is closed.")
        if self.retained is not None and self.retained._lost:
            raise LlmContextLostError("Required retained context was invalidated during execution.")

    def open(self):
        """Calls the full trusted adapter contract only after ER-01 admission."""
        self.requireValid()
        if self.retained is not None and self.scope is not None:
            self.scope.touch(self.retained)
            self.retained._conversation = self.conversation
        self._opened = True
        self._provider = self.binding.adapter.openExecution(self.admission, self.model, self.options)
        if not all(callable(getattr(self._provider, name, None)) for name in ("getExecutionProfile", "prepareContext", "stream")):
            raise LlmProviderProtocolError("Invalid controlled provider session.")
        profile = self._provider.getExecutionProfile()
        if not isinstance(profile, LlmExecutionProfile):
            raise LlmProviderProtocolError("Invalid controlled execution profile.")
        estimator = None if profile.tokenEstimator is None else _Estimator(self, profile.tokenEstimator)
        self._profile = replace(profile, tokenEstimator=estimator)
        self.requireValid()

    @property
    def profile(self):
        """Returns a scoped estimator only while the operation remains valid."""
        self.requireValid()
        return self._profile

    @property
    def profileSnapshot(self):
        """Strips live callables from returned preview/result evidence."""
        return replace(self._profile, tokenEstimator=None)

    @property
    def evidence(self):
        """Returns bounded immutable effects after finalization."""
        return LlmExecutionEvidence(self.claim.token, self._contextEffect,
                                    self._cleanup.effect if self._cleanup else _UNKNOWN, self._iteratorCloseFailed)

    @property
    def previewBinding(self):
        """Returns ephemeral exact provenance, without keeping admission open."""
        result = LlmPreviewBinding(self.profileReference, self.binding.registration, self.claim.token, self.options)
        with self.binding.service._lane:
            self.binding.service._previews.add(result)
        return result

    def stream(self, request):
        """Prepares an owned context and streams through the same admitted provider."""
        self.requireValid()
        effect = self._provider.prepareContext(request.query, self.context)
        if type(effect) is not LlmContextEffect:
            raise LlmProviderProtocolError("Invalid context preparation evidence.")
        self._contextEffect = effect
        if isinstance(self.context, ReuseContext) and effect.status is not EffectStatus.ACKNOWLEDGED:
            raise LlmContextLostError("Required retained context was lost or incompatible.")
        if isinstance(self.context, FreshContext) and self.context.required and effect.status is not EffectStatus.ACKNOWLEDGED:
            raise LlmUnsupportedExecutionError("Required owned logical freshness was not fulfilled.")
        self.requireValid()
        self._iterator = self._provider.stream(request)
        return self._iterator

    def close(self, primary=None, *, _finalize=False):
        """Closes iterator, adapter and claim independently; primary errors survive.

        A successful stream with failed/unknown cleanup raises explicit cleanup
        failure. No retained handle can be returned as usable in that case.
        """
        if not _finalize and not self._closed and _lifetime.get() is not None and (self.access.owner._closed or self.binding.revoked):
            self._pendingReason = LlmSessionExit.CANCELLED if isinstance(primary, ExecutionCancelled) else LlmSessionExit.FAILED
            with self.binding.service._lane:
                self.binding.service._pendingCalls.add(self)
            if primary is not None:
                primary.llmCleanup = self.evidence
                return
            raise LlmExecutionOwnershipError("Execution was revoked; cleanup is deferred outside lifetime locks.")
        with self.binding.service._lane:
            alreadyClosed = self._closed
            self._closed = True
        if alreadyClosed:
            if primary is not None and self._cleanup is not None and self._cleanup.effect != _OK:
                primary.llmCleanup = self.evidence
            return
        iteratorFailed = False
        if self._iterator is not None:
            try:
                closer = getattr(self._iterator, "close", None)
                if closer is not None:
                    closer()
            except Exception:  # noqa: BLE001 - iterator failure must not skip adapter finalization.
                iteratorFailed = True
                self._iteratorCloseFailed = True
        reason = LlmSessionExit.CANCELLED if isinstance(primary, ExecutionCancelled) else (
            LlmSessionExit.FAILED if primary is not None or iteratorFailed else LlmSessionExit.COMPLETED)
        try:
            cleanup = (self.binding.adapter.closeExecution(self.admission, self._provider, reason)
                       if self._opened else LlmSessionCleanup(_OK))
            if type(cleanup) is not LlmSessionCleanup:
                cleanup = LlmSessionCleanup(_UNKNOWN)
        except Exception:  # noqa: BLE001 - absent trusted evidence cannot free residency.
            cleanup = LlmSessionCleanup(_UNKNOWN)
        self._cleanup = cleanup
        cleanupFailed = cleanup.effect != _OK or iteratorFailed
        retentionRequired = isinstance(self.context, ReuseContext) or (isinstance(self.context, FreshContext) and self.context.retain)
        contextLost = retentionRequired and cleanup.contextState is not ContextState.RETAINED
        cleanupFailed = cleanupFailed or contextLost
        try:
            if cleanup.retained is not None:
                # Take ownership even on failure, so partial-open resources are not orphaned.
                pin = self.claim.retain(cleanup.retained.payload, cleanup.retained.disposer)
                if isinstance(pin, ResourceConflict):
                    self._unclaimed = cleanup.retained
                    cleanupFailed = True
                    disposed = _UNKNOWN
                    if cleanup.effect.quiescence is Quiescence.CONFIRMED:
                        try:
                            disposed = cleanup.retained.disposer(cleanup.retained.payload)
                        except Exception:  # noqa: BLE001 - retain partial objects under the exact call ticket.
                            disposed = _UNKNOWN
                    if disposed == _OK:
                        self._unclaimed = None
                    else:
                        cleanup = replace(cleanup, effect=_UNKNOWN)
                        self._cleanup = cleanup
                else:
                    handle = LlmRetainedContextHandle(self, cleanup.retained, pin)
                    self.binding.service._rememberHandle(handle)
                    if self.access.owner._closed or self.binding.revoked or (self.conversation is not None and self.conversation._closed):
                        handle.close()
                        cleanupFailed = True
                    if self.scope is not None:
                        try:
                            self.scope.touch(handle)
                        except LlmExecutionOwnershipError:
                            handle.close()
                            cleanupFailed = True
                    if self.retained is not None or not isinstance(self.context, FreshContext) or not self.context.retain:
                        handle.close()
                        cleanupFailed = True
                    else:
                        self.retainedContext = handle
            if isinstance(self.context, FreshContext) and self.context.retain and self.retainedContext is None:
                cleanupFailed = True
            touched = self._opened or self.retainedContext is not self.retained
            if self.retainedContext is not None and touched and (primary is not None or cleanupFailed or cleanup.contextState is not ContextState.RETAINED):
                self.retainedContext.close()
            if isinstance(self.context, ReuseContext) and cleanup.contextState is not ContextState.RETAINED:
                cleanupFailed = True
        finally:
            self.claim.finish(cleanup.effect)
            self.binding.service._callFinished(self)
        if primary is not None:
            primary.llmCleanup = self.evidence
        elif contextLost and cleanup.effect == _OK and not iteratorFailed:
            error = LlmContextLostError("Required retained state was lost during execution cleanup.")
            error.llmCleanup = self.evidence
            raise error
        elif cleanupFailed:
            raise LlmExecutionCleanupError(cleanup.effect, iteratorCloseFailed=iteratorFailed)


@dataclass(eq=False, slots=True)
class _BindingPublication:
    """Reserves one construction attempt that every relevant revocation can defeat."""

    registry: object
    registration: object
    owners: tuple
    revoked: bool = False


class LlmExecutionBinding:
    """Exact-registration trusted adapter binding and its owned lifetime lease."""

    def __init__(self, service, registry, registration, domain, profiles, adapter, grants, leaseCloser):
        """Creates inert private state; bind's ticket owns unpublished participants."""
        self.service, self.registry, self.registration = service, registry, registration
        self.domain, self.profiles, self.adapter = domain, dict(profiles), adapter
        self.grants, self.participants = dict(grants), {}
        self.leaseCloser = leaseCloser
        self.revoked = False
        self.leaseAttempted = False
        self.leaseFailed = False
        self.calls = set()
        self.handles = set()
        self.conversations = set()

    def requireLive(self):
        """Withdrawal/name reuse cannot turn an already-resolved controlled call legacy."""
        if self.revoked:
            raise LlmExecutionOwnershipError("Controlled provider binding is revoked.")
        try:
            registration = self.registry.requireRegistration(self.registration.name)
        except LookupError:
            self.service.revokeBinding(self)
            raise LlmExecutionOwnershipError("Controlled provider registration is withdrawn.") from None
        if registration is not self.registration:
            self.service.revokeBinding(self)
            raise LlmExecutionOwnershipError("Controlled provider registration was replaced.")

    def profile(self, access, selector):
        """Checks the helper/caller's own explicit selector grants before issuance."""
        if selector is not None and type(selector) is not str:
            raise TypeError("Model selector must be an exact string or None.")
        access.requireValid()
        self.requireLive()
        owner = access.owner
        grant = self.grants.get(owner)
        if owner._registry is not self.registry or grant is None or selector not in grant.selectors:
            raise LlmExecutionOwnershipError("Profile is not granted to this LLM owner.")
        key = (id(owner), selector)
        with self.service._lane:
            result = self.service._profiles.get((id(self), key))
            if result is None:
                result = LlmProfileReference(self, owner, selector)
                self.service._profiles[(id(self), key)] = result
            return result


class LlmExecutionBindings:
    """Host-rooted bindings/tombstones and callback-free lifetime cleanup queues."""

    def __init__(self):
        """Creates inactive composition; no hardware/provider callback runs here."""
        self._lane = RLock()
        self._bindings = {}
        self._pendingPublications = {}
        self._controlledRegistrations = {}
        self._revokedRegistrations = {}
        self._failedPublicationCleanup = []
        self._owners = WeakSet()
        self._accesses = WeakSet()
        self._profiles = {}
        self._handles = WeakSet()
        self._conversations = WeakSet()
        self._previews = WeakSet()
        self._pendingOwners = set()
        self._pendingPins = set()
        self._pendingConversations = set()
        self._pendingCalls = set()
        self._pendingAccesses = set()
        self._closedRegistries = set()
        self._closed = False

    def owner(self, registry):
        """Trusted runtime issues one actual owner for a loaded CodeEntry incarnation."""
        owner = LlmExecutionOwner(self, registry)
        with self._lane:
            if self._closed or id(registry) in self._closedRegistries:
                raise LlmExecutionOwnershipError("LLM registry admission is closed.")
            self._owners.add(owner)
        return owner

    def invocation(self, owner, cancellationSignal=None):
        """Trusted runtime issues normal-invocation access; strings cannot authorize it."""
        with self._lane:
            if type(owner) is not LlmExecutionOwner or owner not in self._owners or owner._closed:
                raise LlmExecutionOwnershipError("LLM owner is closed or foreign.")
            access = LlmInvocationAccess(owner, cancellationSignal)
            self._accesses.add(access)
            return access

    def bind(self, registry, registration, *, domain, profiles, adapter, grants, leaseCloser):
        """Installs a complete trusted contract; ordinary provider registration cannot bind.

        Composition owns the lease until successful publication. Core never
        invokes an adapter or lease closer under broker/registry/domain locks.
        """
        if not all(callable(getattr(adapter, n, None)) for n in ("openExecution", "closeExecution")) or not callable(leaseCloser):
            raise TypeError("Controlled binding requires open/close adapter and owned lease closer.")
        if any(type(getattr(adapter, n, None)) is not bool for n in ("supportsFresh", "supportsRequiredFresh", "supportsRetention")):
            raise TypeError("Controlled adapter must declare exact context support flags.")
        profiles, grants = dict(profiles), dict(grants)
        if not profiles or any(s is not None and (type(s) is not str or not s) for s in profiles):
            raise ValueError("Profile selectors must be nonempty exact strings or None.")
        key = (id(registry), id(registration))
        ticket = _BindingPublication(registry, registration, tuple(grants))
        with registry.executionPublication() as registrations:
            with self._lane:
                if not any(item is registration for item in registrations):
                    raise LlmExecutionOwnershipError("Binding requires the exact published registration.")
                self._controlledRegistrations[key] = (registry, registration)
                self._validatePublication(registry, registration, profiles, grants)
                if key in self._bindings or key in self._pendingPublications:
                    raise LlmExecutionOwnershipError("Registration already has a binding or publication ticket.")
                self._pendingPublications[key] = ticket
        binding = None
        try:
            binding = LlmExecutionBinding(self, registry, registration, domain, profiles, adapter, grants, leaseCloser)
            for owner, grant in grants.items():
                with self._lane:
                    if ticket.revoked:
                        raise LlmExecutionOwnershipError("Binding publication was revoked during construction.")
                participant = _require(domain.openParticipant(
                    allowedProfiles=tuple(profiles[s] for s in grant.selectors),
                    allowIdleTransition=grant.allowTransition))
                binding.participants[owner] = participant
            # Scope withdrawal/name replacement takes the same registry lane.
            # All revocation scopes take the broker lane or revoke this ticket.
            with registry.executionPublication() as registrations:
                with self._lane:
                    if self._pendingPublications.get(key) is not ticket or ticket.revoked:
                        raise LlmExecutionOwnershipError("Binding publication ticket is revoked or stale.")
                    if not any(item is registration for item in registrations):
                        raise LlmExecutionOwnershipError("Binding registration was withdrawn or replaced.")
                    self._validatePublication(registry, registration, profiles, grants)
                    self._bindings[key] = binding  # The lease transfers exactly here.
                    del self._pendingPublications[key]
            return binding
        except BaseException as primary:
            with self._lane:
                published = binding is not None and self._bindings.get(key) is binding
            if published:
                primary.llmBindingPublished = True
            elif binding is not None:
                failures = []
                for participant in reversed(tuple(binding.participants.values())):
                    try:
                        _require(participant.close())
                    except Exception:  # noqa: BLE001 - attempt every owned rollback without masking the primary.
                        failures.append(participant)
                if failures:
                    with self._lane:
                        self._failedPublicationCleanup.append((registry, tuple(failures)))
                    primary.llmPublicationCleanup = LlmExecutionCleanupError()
            raise
        finally:
            with self._lane:
                if self._pendingPublications.get(key) is ticket:
                    del self._pendingPublications[key]

    def _validatePublication(self, registry, registration, profiles, grants):
        """Rechecks all admission/grant gates while the broker lane is held."""
        key = (id(registry), id(registration))
        if self._closed or id(registry) in self._closedRegistries or key in self._revokedRegistrations:
            raise LlmExecutionOwnershipError("LLM binding admission or registration is revoked.")
        for owner, grant in grants.items():
            if type(owner) is not LlmExecutionOwner or owner not in self._owners or owner._registry is not registry or owner._closed:
                raise LlmExecutionOwnershipError("Binding grant requires an issued live registry owner.")
            if type(grant) is not LlmOwnerGrant or type(grant.allowTransition) is not bool or any(s not in profiles for s in grant.selectors):
                raise ValueError("Invalid host profile grant.")

    def binding(self, registry, registration):
        """Returns a retained exact tombstone, never a heuristic optional-method fallback."""
        with self._lane:
            binding = self._bindings.get((id(registry), id(registration)))
            replaced = binding is None and any(r is registry and item.name == registration.name
                                               for r, item in self._controlledRegistrations.values())
        if replaced:
            raise LlmExecutionOwnershipError("Controlled provider registration requires explicit host rebinding.")
        return binding

    def profile(self, registry, access, providerName, model=None):
        """Issues only already-authorized profile references; no provider IO."""
        if access is None:
            raise LlmExecutionOwnershipError("Controlled execution requires runtime invocation access.")
        binding = self.binding(registry, registry.requireRegistration(providerName))
        if binding is None:
            raise LlmUnsupportedExecutionError("Provider does not support controlled execution.")
        return binding.profile(access, model)

    def _validateProfile(self, access, profile):
        """Rejects fabricated/foreign handles even if their visible fields look alike."""
        if access is None:
            raise LlmExecutionOwnershipError("Profile use requires runtime-issued invocation access.")
        access.requireValid()
        with self._lane:
            issued = type(profile) is LlmProfileReference and any(p is profile for p in self._profiles.values())
        if not issued or profile._owner is not access.owner:
            raise LlmExecutionOwnershipError("Profile handle is foreign or unissued.")
        profile._binding.requireLive()

    def transition(self, access, profile):
        """Uses ER-01's explicit host grant; no caller authorization boolean is accepted."""
        self._validateProfile(access, profile)
        binding = profile._binding
        return _require(binding.participants[access.owner].transitionIdle(
            binding.profiles[profile._selector], cancellation=access.cancellationSignal))

    def reserve(self, access, profile):
        """Wraps one exact ER-01 exclusive reservation across sequential fresh invocations."""
        self._validateProfile(access, profile)
        binding = profile._binding
        reservation = _require(binding.participants[access.owner].reserveExclusive(binding.profiles[profile._selector]))
        result = LlmConversationSession(profile, reservation)
        with self._lane:
            self._conversations.add(result)
            binding.conversations.add(result)
        return result

    def admit(self, registry, registration, access, *, options, model=None, session=None, context=None, preview=None, profileOnly=False):
        """Creates exactly one claim/ticket before any controlled adapter IO."""
        binding = self.binding(registry, registration)
        if binding is None:
            if session is not None or context is not None or preview is not None:
                raise LlmUnsupportedExecutionError("Unbound provider cannot fulfill explicit execution features.")
            return None
        if access is None:
            raise LlmExecutionOwnershipError("Controlled calls require runtime-issued invocation access.")
        binding.requireLive()
        if session is not None:
            with self._lane:
                issued = type(session) is LlmConversationSession and session in self._conversations
            if not issued or session._closed or session._profile._binding is not binding:
                raise LlmExecutionOwnershipError("Conversation session is foreign or closed.")
            profile = session._profile
            if model is not None and model != profile._selector:
                raise LlmExecutionOwnershipError("Conversation profile mismatch.")
            model = profile._selector
            self._validateProfile(access, profile)
        else:
            profile = binding.profile(access, model)
        context = FreshContext() if context is None and not profileOnly else context
        retained = None
        scope = currentMutationScope()
        if profileOnly and context is None:
            pass
        elif type(context) is FreshContext:
            if not binding.adapter.supportsFresh or (context.required and not binding.adapter.supportsRequiredFresh) or (context.retain and not binding.adapter.supportsRetention):
                raise LlmUnsupportedExecutionError("Adapter cannot fulfill requested owned context contract.")
        elif type(context) is ReuseContext:
            retained = context.handle
            with self._lane:
                if type(retained) is not LlmRetainedContextHandle or retained not in self._handles or retained._owner is not access.owner or retained._binding is not binding:
                    raise LlmExecutionOwnershipError("Retained handle is foreign or unissued.")
                if retained._lost or retained._profile is not profile:
                    raise LlmContextLostError("Required retained context is lost or incompatible.")
                if retained._provisional is not None and (scope is None or scope.root is not retained._provisional):
                    raise LlmExecutionOwnershipError("Retained context is provisional in another scope.")
        else:
            raise TypeError("Expected FreshContext or ReuseContext.")
        claim = _require(binding.participants[access.owner].acquire(binding.profiles[profile._selector],
            reservation=session._reservation if session else None, retained=retained._pin if retained else None,
            cancellation=access.cancellationSignal))
        call = LlmCallSession(binding, access, profile, claim, options, model, context, retained, session)
        calls = _boundary.get()
        if calls is None:
            claim.finish(_OK)  # No adapter hook has run.
            raise LlmExecutionOwnershipError("Admission requires an execution boundary.")
        calls.append(call)
        with self._lane:
            binding.calls.add(call)
            if retained is not None:
                if retained._lost:
                    raise LlmContextLostError("Required retained context was invalidated during admission.")
                if retained._provisional is not None and (scope is None or scope.root is not retained._provisional):
                    raise LlmExecutionOwnershipError("Retained context became provisional during admission.")
        if preview is not None:
            if type(preview) is not LlmPreviewBinding or preview not in self._previews or preview.profile is not profile or preview.registration is not registration or preview.residency != claim.token or preview.options != options:
                call.close(LlmStalePreviewError("Preview execution binding is stale."))
                raise LlmStalePreviewError("Preview execution binding is stale.")
        call.open()
        return call

    def borrowEstimate(self, registry, registration, access, model, options, query):
        """Borrows only a live nonce with exact issuer/profile/options and own grants."""
        helper = _helper.get()
        if helper is None or helper.call is None:
            return None
        if not helper.active or access is None:
            raise LlmExecutionOwnershipError("Helper estimation scope is closed or unauthorized.")
        call = helper.call
        access.requireValid()
        if access.owner._registry is not registry or registry is not call.binding.registry or registration is not call.binding.registration:
            raise LlmExecutionOwnershipError("Helper runtime or registration does not match admission.")
        selector = call.model if model is None else model
        profile = call.binding.profile(access, selector)  # Requires the helper's own grant.
        if call.binding.profiles[profile._selector] is not call.binding.profiles[call.profileReference._selector] or options != call.options:
            raise LlmExecutionOwnershipError("Helper profile or options do not match admission.")
        estimator = call.profile.tokenEstimator
        if estimator is None:
            raise LlmUnsupportedExecutionError("Provider has no token estimator.")
        result = estimator.estimateInputTokens(query)
        access.requireValid()
        if not helper.active:
            raise LlmExecutionOwnershipError("Helper estimation scope expired.")
        return result

    def _rememberHandle(self, handle):
        """Keeps concrete cleanup ownership rooted in the host binding table."""
        with self._lane:
            self._handles.add(handle)
            handle._binding.handles.add(handle)

    def _callFinished(self, call):
        """Releases broker lifetime only when ER-01 has confirmed operation drain."""
        with self._lane:
            if call._cleanup.effect.quiescence is Quiescence.CONFIRMED and not call.admission._stopRunning:
                call.binding.calls.discard(call)

    def pendingCalls(self, registry=None):
        """Reports outstanding/unknown call ownership without waiting or provider probes."""
        with self._lane:
            return tuple(c for b in self._bindings.values() if registry is None or b.registry is registry for c in b.calls)

    def revokeRegistry(self, registry, *, permanent=True):
        """Marks a removed/closing runtime without needing its possibly busy mutation lane."""
        with registry.executionPublication() as registrations:
            with self._lane:
                if permanent:
                    self._closedRegistries.add(id(registry))
                for registration in registrations:
                    self._revokedRegistrations[(id(registry), id(registration))] = (registry, registration)
                for ticket in self._pendingPublications.values():
                    if ticket.registry is registry:
                        ticket.revoked = True
                for owner in tuple(o for o in self._owners if o._registry is registry):
                    self.revokeOwner(owner)
                for binding in self._bindings.values():
                    if binding.registry is registry:
                        self.revokeBinding(binding)

    def revokeAll(self):
        """Host shutdown revokes all new controlled admission without invoking callbacks."""
        with self._lane:
            self._closed = True
            for ticket in self._pendingPublications.values():
                ticket.revoked = True
            registries = tuple({o._registry for o in self._owners}
                               | {b.registry for b in self._bindings.values()}
                               | {t.registry for t in self._pendingPublications.values()})
        for registry in registries:
            self.revokeRegistry(registry)

    def _invalidateUnlocked(self, handle):
        """Queues exact owned pin cleanup; no provider callback under a broker lock."""
        handle._lost = True
        self._pendingPins.add(handle)

    def invalidate(self, handle):
        """Revokes a specific context without permitting public evidence assertions."""
        with self._lane:
            if type(handle) is not LlmRetainedContextHandle or handle not in self._handles:
                raise LlmExecutionOwnershipError("Context handle is foreign or unissued.")
            self._invalidateUnlocked(handle)

    def revokeOwner(self, owner):
        """Marks only one owner and its handles for post-lock finalization."""
        with self._lane:
            owner._closed = True
            self._pendingOwners.add(owner)
            for ticket in self._pendingPublications.values():
                if any(issued is owner for issued in ticket.owners):
                    ticket.revoked = True
            for binding in self._bindings.values():
                for handle in binding.handles:
                    if handle._owner is owner:
                        self._invalidateUnlocked(handle)
                for conversation in binding.conversations:
                    if conversation._profile._owner is owner:
                        conversation._closed = True
                        self._pendingConversations.add(conversation)

    def revokeRegistration(self, registry, ownerId):
        """Callback-free exact-provider withdrawal; tombstones never become legacy."""
        with registry.executionPublication() as registrations:
            with self._lane:
                for registration in registrations:
                    if registration.ownerId == ownerId:
                        self._revokedRegistrations[(id(registry), id(registration))] = (registry, registration)
                for ticket in self._pendingPublications.values():
                    if ticket.registry is registry and ticket.registration.ownerId == ownerId:
                        ticket.revoked = True
                for binding in self._bindings.values():
                    if binding.registry is registry and binding.registration.ownerId == ownerId:
                        self.revokeBinding(binding)

    def revokeBinding(self, binding):
        """Revokes only one exact issued registration, leaving any replacement untouched."""
        with self._lane:
            if self._bindings.get((id(binding.registry), id(binding.registration))) is not binding:
                raise LlmExecutionOwnershipError("Controlled binding is foreign or unissued.")
            binding.revoked = True
            for handle in binding.handles:
                self._invalidateUnlocked(handle)
            for conversation in binding.conversations:
                conversation._closed = True
                self._pendingConversations.add(conversation)

    def finishClose(self, registry=None):
        """Drains queued callbacks outside all locks, retaining pending/unknown lifetimes."""
        with self._lane:
            owners = tuple(o for o in self._pendingOwners if registry is None or o._registry is registry)
            pins = tuple(p for p in self._pendingPins if registry is None or p._binding.registry is registry)
            conversations = tuple(c for c in self._pendingConversations if registry is None or c._profile._binding.registry is registry)
            bindings = tuple(b for b in self._bindings.values() if registry is None or b.registry is registry)
            calls = tuple(c for c in self._pendingCalls if registry is None or c.binding.registry is registry)
            accesses = tuple(a for a in self._pendingAccesses if registry is None or a.owner._registry is registry)
            expiredCalls = tuple(c for b in bindings for c in b.calls if c.access in accesses and not c._closed)
            publicationPending = any(registry is None or t.registry is registry for t in self._pendingPublications.values())
            publicationFailed = any(registry is None or r is registry for r, _ in self._failedPublicationCleanup)
        for call in expiredCalls:
            call.claim.requestStop()
        for call in calls:
            primary = (ExecutionCancelled("Revoked execution cancellation.") if call._pendingReason is LlmSessionExit.CANCELLED
                       else LlmExecutionOwnershipError("Revoked execution cleanup."))
            call.close(primary, _finalize=True)
            with self._lane:
                self._pendingCalls.discard(call)
        dispositions = []
        for conversation in conversations:
            dispositions.append(conversation._reservation.close())
        for owner in owners:
            for binding in bindings:
                participant = binding.participants.get(owner)
                if participant is not None:
                    dispositions.append(participant.close())
        for binding in bindings:
            if binding.revoked:
                for participant in binding.participants.values():
                    dispositions.append(participant.close())
        for handle in pins:
            result = handle._pin.close()
            if result.effect == _OK:
                with self._lane:
                    handle._disposed = True
                    handle._resource = None
                    handle._binding.handles.discard(handle)
                    self._pendingPins.discard(handle)
        errors = [LlmExecutionCleanupError() for result in dispositions
                  if result.snapshot.state in (ResourceState.DRAINING, ResourceState.UNKNOWN, ResourceState.FAILED)]
        if publicationPending or publicationFailed:
            errors.append(LlmExecutionCleanupError())
        if any(not c._closed for c in expiredCalls):
            errors.append(LlmExecutionCleanupError())
        for binding in bindings:
            snapshot = binding.domain.snapshot
            with self._lane:
                binding.calls.difference_update(c for c in tuple(binding.calls) if c._closed and c._cleanup is not None
                    and c._cleanup.effect.quiescence is Quiescence.CONFIRMED and not c.admission._stopRunning)
                drained = (snapshot.operations == 0 and snapshot.retainedContexts == 0 and not snapshot.exclusive
                           and snapshot.state in (ResourceState.READY, ResourceState.EMPTY, ResourceState.CLOSED))
                done = binding.revoked and not binding.calls and not binding.handles and drained
                release = done and not binding.leaseAttempted
                if release:
                    binding.leaseAttempted = True
            if release:
                try:
                    binding.leaseCloser()
                except Exception:  # noqa: BLE001 - failed lease disposal is a retained tombstone.
                    binding.leaseFailed = True
            if binding.revoked and (not done or binding.leaseFailed):
                errors.append(LlmExecutionCleanupError())
        with self._lane:
            if any(p in self._pendingPins for p in pins):
                errors.append(LlmExecutionCleanupError())
            for owner in owners:
                if any(c.access.owner is owner for b in bindings for c in b.calls):
                    errors.append(LlmExecutionCleanupError())
        # Domain reads take their own lane; never nest it under the broker lane.
        completed = tuple(c for c in conversations if not c._profile._binding.domain.snapshot.exclusive)
        with self._lane:
            self._pendingConversations.difference_update(completed)
            self._pendingAccesses.difference_update(a for a in accesses
                if not any(c.access is a and not c._closed for b in bindings for c in b.calls))
        if errors:
            raise ExceptionGroup("LLM execution lifetime cleanup is incomplete.", errors)
