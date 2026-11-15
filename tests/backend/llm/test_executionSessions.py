# file: tests/backend/llm/test_executionSessions.py ; version: 1
"""Controlled fake adapters exercise admission, ownership and truthful cleanup."""
from concurrent.futures import Future
from contextvars import copy_context
from threading import Event, Thread

import pytest

from backend.llm.errors import (
    LlmContextLostError, LlmExecutionCleanupError, LlmExecutionConflictError,
    LlmExecutionOwnershipError, LlmStalePreviewError, LlmUnsupportedExecutionError,
)
from backend.llm.executionSessions import (
    ContextState, FreshContext, ReuseContext, LlmContextEffect, LlmMutationScope,
    LlmOwnerGrant, LlmProviderContext, LlmSessionCleanup, _mutation, mutationScope,
)
from backend.llm.llmTypes import LlmExecutionProfile, LlmQuery, LlmStreamEvent
from backend.llm.streamingRuntime import LlmProcessingPipeline, LlmProviderRegistry
from backend.orchestration.cancellation import CancellationSignal, ExecutionCancelled
from backend.processing.runtime import QueryItem
from backend.registration import RegistrationScope
from backend.runtime.executionResources import (
    EffectStatus, ExecutionResourceDomain, Materialization, ProviderEffect,
    Quiescence, ResidencyDescriptor, ResourceState,
)
from backend.values.committed import CommittedValueLayer
from backend.values.sentinels import MISSING

OK = ProviderEffect(EffectStatus.ACKNOWLEDGED, Quiescence.CONFIRMED)
UNKNOWN = ProviderEffect(EffectStatus.UNKNOWN, Quiescence.UNKNOWN)
QUERY = LlmQuery(formatId="text/plain", payload="history")


def background(callback):
    """Returns a Future containing actual exceptions; daemon bounds broken tests."""
    result = Future()

    def invoke():
        """Records exact asynchronous results without relying on sleeps."""
        try:
            result.set_result(callback())
        except BaseException as error:
            result.set_exception(error)

    Thread(target=invoke, daemon=True).start()
    return result


class Residency:
    """Fake materialization boundary with no process/server or hardware effects."""

    def __init__(self):
        """Records all actual residency transitions."""
        self.loads, self.releases = [], []
        self.token = None

    def materialize(self, descriptor, token):
        """Makes this synthetic generation resident."""
        self.loads.append(token)
        self.token = token
        return Materialization(OK, True, object())

    def release(self, token, payload):
        """Records release of the exact owned synthetic object."""
        self.releases.append(token)
        self.token = None
        return OK


class Legacy:
    """Existing unbound behavior; controlled calls must never use these methods."""

    def __init__(self):
        """Tracks any legacy fallback."""
        self.calls = []

    def getExecutionProfile(self, **kwargs):
        """Provides unchanged legacy execution constraints."""
        self.calls.append("profile")
        return LlmExecutionProfile(contextWindowTokens=1024)

    def stream(self, request):
        """Provides unchanged legacy event behavior."""
        self.calls.append("stream")
        yield LlmStreamEvent("completed")


class Adapter:
    """Full fake controlled contract with hook-controlled deterministic failures."""

    supportsFresh = True
    supportsRequiredFresh = True
    supportsRetention = True

    def __init__(self, residency):
        """Creates independent per-ticket state and precise effect records."""
        self.residency = residency
        self.opens, self.closes, self.actions, self.disposals = [], [], [], []
        self.onOpen = self.onProfile = self.onEstimate = self.onStream = self.onClose = None
        self.cleanup = OK
        self.closeIteratorFails = False
        self.savedEstimator = None

    def openExecution(self, admission, model, options):
        """Records ticket ownership before a possible partial-open exception."""
        self.opens.append(admission)
        if self.onOpen:
            self.onOpen(admission)
        return ProviderSession(self, admission)

    def closeExecution(self, admission, session, reason):
        """Finalizes exactly one ticket, with optional retained-state transfer."""
        self.closes.append((admission, reason))
        if self.onClose:
            return self.onClose(admission, session, reason)
        context = session.context if session is not None else None
        if isinstance(context, FreshContext) and context.retain:
            resource = LlmProviderContext(object(), self.dispose)
            return LlmSessionCleanup(self.cleanup, ContextState.RETAINED, resource)
        if isinstance(context, ReuseContext):
            return LlmSessionCleanup(self.cleanup, ContextState.RETAINED)
        return LlmSessionCleanup(self.cleanup)

    def dispose(self, payload):
        """Proves exactly-once disposal of owned context objects."""
        self.disposals.append(payload)
        return OK


class ProviderSession:
    """All provider phases assert the exact admitted residency is still current."""

    def __init__(self, adapter, admission):
        """Keeps only a trusted adapter ticket and current context request."""
        self.adapter, self.admission = adapter, admission
        self.context = None

    def check(self, phase):
        """Records exact generation in every provider-facing phase."""
        assert self.adapter.residency.token is self.admission.residency
        self.adapter.actions.append((phase, self.admission.residency))

    def getExecutionProfile(self):
        """Returns a tokenizer whose subsequent use is residency-sensitive."""
        self.check("profile")
        if self.adapter.onProfile:
            self.adapter.onProfile(self)
        return LlmExecutionProfile(contextWindowTokens=1024, tokenEstimator=Estimator(self))

    def prepareContext(self, query, request):
        """Acknowledges only the owned logical fresh/reuse action requested."""
        self.check("context")
        self.context = request
        return LlmContextEffect(EffectStatus.ACKNOWLEDGED)

    def stream(self, request):
        """Returns an iterator with observable consumer-close behavior."""
        self.check("stream")
        return Stream(self)


class Estimator:
    """Synthetic tokenization uses the same residency as profile selection."""

    def __init__(self, session):
        """Binds tokenization to the exact opened provider session."""
        self.session = session

    def estimateInputTokens(self, query):
        """Allows a competing transition between profile and tokenization."""
        self.session.check("estimate")
        if self.session.adapter.onEstimate:
            self.session.adapter.onEstimate(self.session)
        return 7


class Stream:
    """Explicit iterator makes close failure and blocked streaming testable."""

    def __init__(self, session):
        """Creates one unconsumed fake response."""
        self.session, self.index, self.closed = session, 0, False

    def __iter__(self):
        """Preserves the exact synthetic response identity."""
        return self

    def __next__(self):
        """Yields one delta and terminal event; hooks can pause the active response."""
        self.index += 1
        if self.index == 1:
            if self.session.adapter.onStream:
                self.session.adapter.onStream(self.session)
            return LlmStreamEvent("delta", text="ok")
        if self.index == 2:
            return LlmStreamEvent("completed")
        raise StopIteration

    def close(self):
        """Records local transport closure, which is not provider quiescence evidence."""
        self.closed = True
        if self.session.adapter.closeIteratorFails:
            raise ValueError("private iterator failure")


def fixture(*, registry=None, loaded=True, owners=None, domain=None, residency=None, coreProfiles=None):
    """Builds host-approved profiles and two separately granted CodeEntry owners."""
    registry = registry or LlmProviderRegistry()
    scope = RegistrationScope()
    legacy = Legacy()
    registry.register(scope, ownerId="provider-owner", name="controlled", provider=legacy)
    scope.publish()
    service = registry.executions
    owner, peer = owners if owners is not None else (service.owner(registry), service.owner(registry))
    residency = residency or Residency()
    domain = domain or ExecutionResourceDomain("fake", residency)
    a = coreProfiles[None] if coreProfiles else domain.registerProfile(ResidencyDescriptor(b"A", object()))
    b = coreProfiles["B"] if coreProfiles else domain.registerProfile(ResidencyDescriptor(b"B", object()))
    adapter = Adapter(residency)
    leases = []
    binding = service.bind(registry, registry.requireRegistration("controlled"), domain=domain,
        profiles={None: a, "B": b}, adapter=adapter,
        grants={owner: LlmOwnerGrant((None, "B"), True), peer: LlmOwnerGrant((None, "B"), True)},
        leaseCloser=lambda: leases.append(1))
    access, peerAccess = owner.invocation(), peer.invocation()
    if loaded:
        service.transition(access, service.profile(registry, access, "controlled"))
    pipeline = LlmProcessingPipeline(providers=registry)
    return pipeline, service, domain, binding, adapter, legacy, owner, peer, access, peerAccess, scope, leases


def test_same_generation_across_profile_estimation_context_stream_cleanup():
    """No phase can escape admission; public results never retain a live estimator."""
    pipeline, service, domain, binding, adapter, legacy, owner, _, access, _, _, _ = fixture()
    result = pipeline.run(providerName="controlled", query=QUERY, executionAccess=access)
    assert result.rawText == "ok" and result.executionProfile.tokenEstimator is None
    assert result.executionEvidence.residency is domain.snapshot.residency
    assert len(adapter.opens) == len(adapter.closes) == 1
    assert {token.generation for _, token in adapter.actions} == {1}
    assert domain.snapshot.operations == 0 and legacy.calls == []


def test_active_stream_prevents_residency_switch_original_race():
    """Architectural Test: an open A response cannot be terminated by B admission."""
    pipeline, service, domain, binding, adapter, _, _, _, access, peer, _, _ = fixture()
    entered, proceed = Event(), Event()

    def hold(session):
        """Holds the actual provider response open at a deterministic event."""
        entered.set()
        assert proceed.wait(5)

    adapter.onStream = hold
    pending = background(lambda: pipeline.run(providerName="controlled", query=QUERY, executionAccess=access))
    try:
        assert entered.wait(5)
        with pytest.raises(LlmExecutionConflictError):
            service.transition(peer, service.profile(binding.registry, peer, "controlled", "B"))
        assert adapter.residency.releases == []
    finally:
        proceed.set()
    assert pending.result(5).rawText == "ok"
    assert domain.snapshot.state is ResourceState.READY


def test_profile_to_estimator_binding_prevents_original_estimator_race():
    """Architectural Test: the estimator obtained for A cannot tokenize against B."""
    pipeline, service, domain, binding, adapter, _, _, _, access, peer, _, _ = fixture()
    entered, proceed = Event(), Event()

    def hold(session):
        """Pauses after profile selection and before tokenization."""
        entered.set()
        assert proceed.wait(5)

    adapter.onProfile = hold
    pending = background(lambda: pipeline.estimateInputTokens(providerName="controlled", query=QUERY, executionAccess=access))
    try:
        assert entered.wait(5)
        with pytest.raises(LlmExecutionConflictError):
            service.transition(peer, service.profile(binding.registry, peer, "controlled", "B"))
    finally:
        proceed.set()
    assert pending.result(5) == 7
    assert [phase for phase, _ in adapter.actions] == ["profile", "estimate"]
    assert domain.snapshot.operations == 0


def test_exclusive_conversation_across_invocations_and_nonreentrant_call():
    """The reservation survives invocation closure but every turn needs fresh access."""
    pipeline, service, _, binding, adapter, _, owner, _, access, peer, _, _ = fixture()
    conversation = service.reserve(access, service.profile(binding.registry, access, "controlled"))
    access.close()
    with pytest.raises(LlmExecutionOwnershipError):
        pipeline.run(providerName="controlled", query=QUERY, executionAccess=access, session=conversation)
    nextAccess = owner.invocation()

    def nested(session):
        """A recursive call cannot borrow the parent's exclusive operation."""
        with pytest.raises(LlmExecutionConflictError):
            pipeline.run(providerName="controlled", query=QUERY, executionAccess=nextAccess, session=conversation)
        with pytest.raises(LlmExecutionConflictError):
            pipeline.run(providerName="controlled", query=QUERY, executionAccess=peer)

    adapter.onStream = nested
    assert pipeline.run(providerName="controlled", query=QUERY, executionAccess=nextAccess, session=conversation).rawText == "ok"
    conversation.close()
    service.finishClose()
    assert len(adapter.opens) == 1


def test_retained_context_pins_between_calls_and_required_loss_never_rebuilds():
    """Weight residency is distinct from owned retained inference context."""
    pipeline, service, domain, binding, adapter, _, owner, _, access, peer, _, _ = fixture()
    result = pipeline.run(providerName="controlled", query=QUERY, executionAccess=access, context=FreshContext(retain=True))
    handle = result.retainedContext
    assert handle.contextGeneration == 1 and domain.snapshot.retainedContexts == 1
    with pytest.raises(LlmExecutionConflictError):
        service.transition(peer, service.profile(binding.registry, peer, "controlled", "B"))
    with pytest.raises(LlmExecutionOwnershipError):
        pipeline.run(providerName="controlled", query=QUERY, executionAccess=peer, context=ReuseContext(handle))
    assert not handle._lost
    access.close()
    pipeline.run(providerName="controlled", query=QUERY, executionAccess=owner.invocation(), context=ReuseContext(handle))
    handle.invalidate()
    with pytest.raises(LlmContextLostError):
        pipeline.run(providerName="controlled", query=QUERY, executionAccess=owner.invocation(), context=ReuseContext(handle))
    service.finishClose()
    assert len(adapter.disposals) == 1 and len(adapter.residency.loads) == 1


@pytest.mark.parametrize("phase", ("open", "profile", "estimate", "stream"))
def test_partial_failures_always_finalize_exact_ticket_once(phase):
    """The sole finalizer owns partial open even before a provider session is returned."""
    pipeline, _, domain, _, adapter, _, _, _, access, _, _, _ = fixture()

    def fail(*args):
        """Produces an application-visible primary failure without fake quiescence."""
        raise ValueError("primary")

    setattr(adapter, "on" + phase.title(), fail)
    method = pipeline.estimateInputTokens if phase == "estimate" else pipeline.run
    with pytest.raises(ValueError, match="primary") as failure:
        method(providerName="controlled", query=QUERY, executionAccess=access)
    assert failure.value.llmCleanup.cleanup == OK
    assert len(adapter.opens) == len(adapter.closes) == 1
    assert domain.snapshot.operations == 0


def test_iterator_close_exception_does_not_skip_adapter_or_claim():
    """Explicit cleanup error can coexist with confirmed provider quiescence."""
    pipeline, _, domain, _, adapter, _, _, _, access, _, _, _ = fixture()
    adapter.closeIteratorFails = True
    with pytest.raises(LlmExecutionCleanupError):
        pipeline.run(providerName="controlled", query=QUERY, executionAccess=access)
    assert len(adapter.closes) == 1 and domain.snapshot.operations == 0


@pytest.mark.parametrize("cancel", (False, True))
def test_unknown_cleanup_is_explicit_and_never_silently_reusable(cancel):
    """Best-effort effects do not weaken mandatory unknown-quiescence blocking."""
    pipeline, _, domain, _, adapter, _, owner, _, access, _, _, _ = fixture()
    adapter.cleanup = UNKNOWN
    if cancel:
        signal = CancellationSignal()
        access = owner.invocation(signal)
        adapter.onStream = lambda session: signal.request()
    with pytest.raises(ExecutionCancelled if cancel else LlmExecutionCleanupError) as failure:
        pipeline.run(providerName="controlled", query=QUERY, executionAccess=access)
    assert domain.snapshot.state is ResourceState.UNKNOWN
    if cancel:
        assert failure.value.llmCleanup.cleanup is UNKNOWN
    assert len(adapter.closes) == 1


@pytest.mark.parametrize("feature", ("retention", "freshness"))
def test_unsupported_stronger_context_contract_is_rejected_before_provider_io(feature):
    """Required retained/fresh behavior cannot be silently downgraded."""
    pipeline, _, domain, _, adapter, _, _, _, access, _, _, _ = fixture()
    adapter.supportsRetention = feature != "retention"
    adapter.supportsRequiredFresh = feature != "freshness"
    with pytest.raises(LlmUnsupportedExecutionError):
        pipeline.run(providerName="controlled", query=QUERY, executionAccess=access,
                     context=FreshContext(retain=feature == "retention", required=feature == "freshness"))
    assert adapter.opens == [] and domain.snapshot.operations == 0


def test_exact_registration_tombstone_never_falls_back_after_withdrawal():
    """Same provider/name does not reuse the authority of a withdrawn registration."""
    pipeline, service, _, binding, adapter, legacy, _, _, access, _, scope, leases = fixture()
    registration = binding.registration
    service.revokeRegistration(binding.registry, registration.ownerId)
    scope.withdraw()
    replacement = RegistrationScope()
    binding.registry.register(replacement, ownerId="provider-owner", name="controlled", provider=legacy)
    replacement.publish()
    with pytest.raises(LlmExecutionOwnershipError):
        binding.requireLive()
    assert service.binding(binding.registry, registration) is binding
    service.finishClose()
    assert leases == [1] and legacy.calls == []


def processing(pipeline, callback=None):
    """Adds real committed-state preparation with configurable helper behavior."""
    state = CommittedValueLayer()

    def invoke(capabilityId, payload, transaction):
        """Produces stable reusable query material and an optional failing completion."""
        if callback:
            callback(capabilityId, payload, transaction)
        if capabilityId == "items@1":
            return [QueryItem(itemId="item", kind="text", content="history")]
        if capabilityId == "query@1":
            return {"formatId": "text/plain", "payload": "history"}
        return None

    pipeline._state, pipeline._capabilityInvoker = state, invoke
    kwargs = dict(memoryKey="chat", inputValue=None, buildQueryItemsCapabilityId="items@1",
                  buildQueryCapabilityId="query@1", providerName="controlled")
    return state, kwargs


def test_preview_releases_call_strips_estimator_and_rejects_changed_generation():
    """Standalone preparation is not a durable residency pin or a live estimator."""
    pipeline, service, domain, binding, adapter, _, _, _, access, _, _, _ = fixture()
    _, kwargs = processing(pipeline)
    preview = pipeline.prepareProcessing(**kwargs, executionAccess=access)
    assert preview.inputTokens == 7 and preview.executionProfile.tokenEstimator is None
    assert domain.snapshot.operations == 0
    assert all(phase != "context" for phase, _ in adapter.actions)
    service.transition(access, service.profile(binding.registry, access, "controlled", "B"))
    service.transition(access, service.profile(binding.registry, access, "controlled"))
    opened = len(adapter.opens)
    with pytest.raises(LlmStalePreviewError):
        pipeline.runPreview(preview, executionAccess=access)
    assert len(adapter.opens) == opened and domain.snapshot.operations == 0


def test_preview_valid_execution_and_foreign_owner_rejection():
    """Revalidated execution uses exact preview ownership and its current generation."""
    pipeline, _, _, _, _, _, _, _, access, peer, _, _ = fixture()
    _, kwargs = processing(pipeline)
    preview = pipeline.prepareProcessing(**kwargs, executionAccess=access)
    with pytest.raises(LlmStalePreviewError):
        pipeline.runPreview(preview, executionAccess=peer)
    assert pipeline.runPreview(preview, executionAccess=access).rawText == "ok"


def test_processing_abort_invalidates_only_admitted_mutations_and_rolls_back_values():
    """Completion failure cannot publish retained state as committed transcript."""
    pipeline, service, domain, _, adapter, _, _, _, access, _, _, _ = fixture()

    def failCompletion(capabilityId, payload, transaction):
        """Fails after stream cleanup, at the actual transactional completion boundary."""
        if capabilityId == "complete@1":
            raise ValueError("completion")

    state, kwargs = processing(pipeline, failCompletion)
    with pytest.raises(ValueError, match="completion"):
        pipeline.runProcessing(**kwargs, executionAccess=access, context=FreshContext(retain=True),
                               completionCapabilityId="complete@1")
    assert state.load("processing/chat/lastrun") is MISSING
    service.finishClose()
    assert domain.snapshot.retainedContexts == 0 and len(adapter.disposals) == 1


def test_provisional_context_guard_and_stale_abort_receipts():
    """Another root cannot reuse provisional state; stale receipts cannot erase later state."""
    pipeline, service, _, _, _, _, _, _, access, _, _, _ = fixture()
    with mutationScope() as outer:
        handle = pipeline.run(providerName="controlled", query=QUERY, executionAccess=access,
                              context=FreshContext(retain=True)).retainedContext
        copied = copy_context()
        token = _mutation.set(None)
        try:
            with pytest.raises(LlmExecutionOwnershipError, match="provisional"):
                pipeline.run(providerName="controlled", query=QUERY, executionAccess=access, context=ReuseContext(handle))
        finally:
            _mutation.reset(token)
        pipeline.run(providerName="controlled", query=QUERY, executionAccess=access, context=ReuseContext(handle))
    with pytest.raises(LlmExecutionOwnershipError, match="expired"):
        copied.run(lambda: pipeline.run(providerName="controlled", query=QUERY, executionAccess=access))
    stale = LlmMutationScope()
    stale.receipts.append((handle, handle._revision))
    pipeline.run(providerName="controlled", query=QUERY, executionAccess=access, context=ReuseContext(handle))
    stale.resolve(False)
    assert not handle._lost


def test_outer_scope_abort_after_inner_return_invalidates_retained_state():
    """The outer disposition owns provisional handles despite successful inner return."""
    pipeline, service, domain, _, adapter, _, _, _, access, _, _, _ = fixture()
    with pytest.raises(ValueError):
        with mutationScope():
            handle = pipeline.run(providerName="controlled", query=QUERY, executionAccess=access,
                                  context=FreshContext(retain=True)).retainedContext
            raise ValueError("outer")
    assert handle._lost
    service.finishClose()
    assert len(adapter.disposals) == 1 and domain.snapshot.retainedContexts == 0


def test_helper_uses_own_grants_and_exact_borrowed_generation():
    """Read-only helper borrowing is scoped and never blanket same-runtime authority."""
    pipeline, service, _, binding, adapter, _, _, peer, access, helperAccess, _, _ = fixture()
    seen = []

    def queryHelper(capabilityId, payload, transaction):
        """Borrows the parent call only during its query-building callback."""
        if capabilityId == "query@1":
            seen.append(pipeline.estimateInputTokens(providerName="controlled", query=QUERY, executionAccess=helperAccess))

    _, kwargs = processing(pipeline, queryHelper)
    pipeline.runProcessing(**kwargs, executionAccess=access)
    assert seen == [7] and len(adapter.opens) == 1
    binding.grants.pop(peer)
    with pytest.raises(LlmExecutionOwnershipError):
        pipeline.runProcessing(**kwargs, executionAccess=access)


def test_legacy_defaults_preserved_and_explicit_features_rejected():
    """An unbound provider retains behavior without making any residency promise."""
    registry = LlmProviderRegistry()
    scope = RegistrationScope()
    legacy = Legacy()
    registry.register(scope, ownerId="legacy", name="old", provider=legacy)
    scope.publish()
    pipeline = LlmProcessingPipeline(providers=registry)
    assert pipeline.run(providerName="old", query=QUERY).executionEvidence is None
    with pytest.raises(LlmUnsupportedExecutionError):
        pipeline.run(providerName="old", query=QUERY, context=FreshContext())
    assert legacy.calls == ["profile", "stream"]


def test_replacement_controlled_name_requires_explicit_host_rebinding():
    """Name/provider reuse cannot silently reintroduce an unmanaged fallback path."""
    pipeline, service, _, binding, adapter, legacy, owner, _, access, _, scope, _ = fixture()
    service.revokeRegistration(binding.registry, "provider-owner")
    scope.withdraw()
    newScope = RegistrationScope()
    binding.registry.register(newScope, ownerId="new", name="controlled", provider=legacy)
    newScope.publish()
    with pytest.raises(LlmExecutionOwnershipError, match="rebinding"):
        pipeline.run(providerName="controlled", query=QUERY, executionAccess=access)
    assert legacy.calls == [] and adapter.opens == []


def test_provisional_state_is_rechecked_after_the_core_claim_is_acquired():
    """A root change between preflight and acquire cannot consume uncommitted state."""
    pipeline, service, _, binding, _, _, owner, _, access, _, _, _ = fixture()
    handle = pipeline.run(providerName="controlled", query=QUERY, executionAccess=access,
                          context=FreshContext(retain=True)).retainedContext
    participant = binding.participants[owner]
    original = participant.acquire
    entered, proceed = Event(), Event()
    held = []

    def acquire(*args, **kwargs):
        """Holds the first contender after its provisional preflight, before core admission."""
        if not held:
            held.append(1)
            entered.set()
            assert proceed.wait(5)
        return original(*args, **kwargs)

    participant.acquire = acquire
    contender = background(lambda: pipeline.run(providerName="controlled", query=QUERY,
        executionAccess=access, context=ReuseContext(handle)))
    try:
        assert entered.wait(5)
        with mutationScope():
            pipeline.run(providerName="controlled", query=QUERY, executionAccess=access, context=ReuseContext(handle))
            proceed.set()
            with pytest.raises(LlmExecutionOwnershipError, match="provisional"):
                contender.result(5)
            assert not handle._lost
    finally:
        proceed.set()
        participant.acquire = original


def test_partial_context_transfer_after_cancel_is_disposed_or_kept_unknown():
    """Revoked admission cannot orphan a resource returned by late provider cleanup."""
    pipeline, service, domain, _, adapter, _, owner, _, _, _, _, _ = fixture()
    signal = CancellationSignal()
    access = owner.invocation(signal)
    adapter.onStream = lambda session: signal.request()
    with pytest.raises(ExecutionCancelled):
        pipeline.run(providerName="controlled", query=QUERY, executionAccess=access, context=FreshContext(retain=True))
    assert len(adapter.disposals) == 1 and domain.snapshot.operations == 0
    assert domain.snapshot.retainedContexts == 0


def test_saved_estimator_and_copied_helper_nonce_expire_with_the_scope():
    """A copied ContextVar and saved callable cannot extend provider session authority."""
    pipeline, _, _, _, adapter, _, _, _, access, helperAccess, _, _ = fixture()
    saved = []

    def build(capabilityId, payload, transaction):
        """Captures the actual borrow scope while it is valid."""
        if capabilityId == "query@1":
            from backend.llm.executionSessions import _helper
            saved.append((copy_context(), _helper.get().call.profile.tokenEstimator))

    _, kwargs = processing(pipeline, build)
    pipeline.runProcessing(**kwargs, executionAccess=access)
    with pytest.raises(LlmExecutionOwnershipError):
        saved[0][1].estimateInputTokens(QUERY)
    with pytest.raises(LlmExecutionOwnershipError):
        saved[0][0].run(lambda: pipeline.estimateInputTokens(providerName="controlled", query=QUERY,
                                                            executionAccess=helperAccess))


@pytest.mark.parametrize("mismatch", ("runtime", "options", "profile"))
def test_helper_estimation_does_not_borrow_mismatched_authority(mismatch):
    """Actual issuer, profile and frozen options all constrain borrowed estimation."""
    pipeline, service, _, binding, adapter, _, _, _, access, helperAccess, _, _ = fixture()
    foreign = LlmProviderRegistry()
    if mismatch == "runtime":
        other = fixture(registry=foreign)

    def build(capabilityId, payload, transaction):
        """Attempts a specific unauthorized helper borrow before provider IO."""
        if capabilityId == "query@1":
            if mismatch == "runtime":
                other[0].estimateInputTokens(providerName="controlled", query=QUERY, executionAccess=other[8])
            else:
                pipeline.estimateInputTokens(providerName="controlled", query=QUERY, executionAccess=helperAccess,
                    providerOptions={"temperature": 1} if mismatch == "options" else None,
                    model="B" if mismatch == "profile" else None)

    _, kwargs = processing(pipeline, build)
    with pytest.raises(LlmExecutionOwnershipError):
        pipeline.runProcessing(**kwargs, executionAccess=access)
    assert len(adapter.opens) == 1


def test_commit_failure_disposes_unpublished_context_without_persisting_run(monkeypatch):
    """A failing Value commit cannot leak the newly retained provider artifact."""
    from backend.values.committed import CommittedValueTransaction
    pipeline, service, domain, _, adapter, _, _, _, access, _, _, _ = fixture()
    state, kwargs = processing(pipeline)

    def failCommit(transaction):
        """Fails at the actual commit boundary after provider cleanup."""
        raise ValueError("commit failed")

    monkeypatch.setattr(CommittedValueTransaction, "commit", failCommit)
    with pytest.raises(ValueError, match="commit failed"):
        pipeline.runProcessing(**kwargs, executionAccess=access, context=FreshContext(retain=True))
    service.finishClose()
    assert state.load("processing/chat/lastrun") is MISSING
    assert len(adapter.disposals) == 1 and domain.snapshot.retainedContexts == 0


def test_conversation_close_invalidates_and_disposes_its_retained_children():
    """Closing an outer reservation cannot leave its disposed child handle looking live."""
    pipeline, service, domain, binding, adapter, _, _, _, access, _, _, _ = fixture()
    conversation = service.reserve(access, service.profile(binding.registry, access, "controlled"))
    handle = pipeline.run(providerName="controlled", query=QUERY, executionAccess=access,
        session=conversation, context=FreshContext(retain=True)).retainedContext
    conversation.close()
    assert handle._lost
    service.finishClose()
    assert domain.snapshot.retainedContexts == 0 and len(adapter.disposals) == 1
    assert handle._resource is None


def test_one_application_binding_close_does_not_stop_peer_same_domain_operation():
    """Only the closing registry's owners are revoked; a peer keeps its active generation."""
    first = fixture()
    pipeline, service, domain, binding, adapter, _, _, _, access, _, _, leases = first
    registry = LlmProviderRegistry()
    registry.executions = service
    second = fixture(registry=registry, domain=domain, residency=adapter.residency, coreProfiles=binding.profiles)
    entered, proceed = Event(), Event()

    def hold(session):
        """Keeps peer work active while the first application detaches."""
        entered.set()
        assert proceed.wait(5)

    second[4].onStream = hold
    pending = background(lambda: second[0].run(providerName="controlled", query=QUERY, executionAccess=second[8]))
    try:
        assert entered.wait(5)
        service.revokeRegistry(binding.registry)
        with pytest.raises(ExceptionGroup):
            service.finishClose(binding.registry)
        assert not second[3].revoked and not second[6]._closed
        assert leases == [] and adapter.residency.releases == []
    finally:
        proceed.set()
    assert pending.result(5).rawText == "ok"
    service.finishClose(binding.registry)
    assert leases == [1] and second[-1] == []


def test_primary_error_retains_classification_and_safe_iterator_cleanup_evidence():
    """Even a failed local iterator close must not mask the original provider exception."""
    pipeline, _, domain, _, adapter, _, _, _, access, _, _, _ = fixture()

    def fail(session):
        """Raises the original provider error before iterator consumption can complete."""
        raise LookupError("primary stream failure")

    adapter.onStream, adapter.closeIteratorFails = fail, True
    with pytest.raises(LookupError, match="primary stream failure") as failure:
        pipeline.run(providerName="controlled", query=QUERY, executionAccess=access)
    assert failure.value.llmCleanup.iteratorCloseFailed
    assert len(adapter.closes) == 1 and domain.snapshot.operations == 0


def test_adapter_close_exception_retains_unknown_claim_and_never_falls_back():
    """An adapter finalizer failure has no invented evidence of stopped execution."""
    pipeline, _, domain, _, adapter, legacy, _, _, access, _, _, _ = fixture()

    def fail(*args):
        """Simulates finalizer failure with private error text excluded from evidence."""
        raise ValueError("private cleanup detail")

    adapter.onClose = fail
    with pytest.raises(LlmExecutionCleanupError) as failure:
        pipeline.run(providerName="controlled", query=QUERY, executionAccess=access)
    assert domain.snapshot.state is ResourceState.UNKNOWN and legacy.calls == []
    assert "private" not in repr(failure.value.llmCleanup)


def test_required_retained_loss_during_cleanup_is_explicit_and_disposed():
    """A successful response cannot advertise a valid handle after reported state loss."""
    pipeline, service, _, _, adapter, _, _, _, access, _, _, _ = fixture()
    handle = pipeline.run(providerName="controlled", query=QUERY, executionAccess=access,
        context=FreshContext(retain=True)).retainedContext
    adapter.onClose = lambda *args: LlmSessionCleanup(OK, ContextState.LOST)
    with pytest.raises(LlmContextLostError):
        pipeline.run(providerName="controlled", query=QUERY, executionAccess=access, context=ReuseContext(handle))
    service.finishClose()
    assert handle._lost and len(adapter.disposals) == 1


def test_copied_helper_nonce_expires_while_parent_call_is_still_active():
    """A live parent operation does not extend a completed helper's borrow nonce."""
    pipeline, _, _, _, adapter, _, _, _, access, helperAccess, _, _ = fixture()
    saved = []
    entered, proceed = Event(), Event()

    def build(capabilityId, payload, transaction):
        """Copies the helper ContextVar while the callback is legitimately in scope."""
        if capabilityId == "query@1":
            saved.append(copy_context())

    def hold(session):
        """Keeps the parent operation live after the query helper has already returned."""
        entered.set()
        assert proceed.wait(5)

    _, kwargs = processing(pipeline, build)
    adapter.onStream = hold
    pending = background(lambda: pipeline.runProcessing(**kwargs, executionAccess=access))
    try:
        assert entered.wait(5)
        with pytest.raises(LlmExecutionOwnershipError, match="closed or unauthorized"):
            saved[0].run(lambda: pipeline.estimateInputTokens(providerName="controlled", query=QUERY,
                                                            executionAccess=helperAccess))
        assert len(adapter.opens) == 1
    finally:
        proceed.set()
    assert pending.result(5).llm.rawText == "ok"


def test_nested_independent_domain_is_not_blocked_by_outer_exclusive_conversation():
    """Exclusive admission is domain-scoped, not an accidental global runtime lock."""
    pipeline, service, _, binding, adapter, _, _, _, access, _, _, _ = fixture()
    other = fixture()
    conversation = service.reserve(access, service.profile(binding.registry, access, "controlled"))
    results = []

    def nested(session):
        """A separately authorized controlled domain obtains its own ER-01 claim."""
        results.append(other[0].run(providerName="controlled", query=QUERY, executionAccess=other[8]))

    adapter.onStream = nested
    pipeline.run(providerName="controlled", query=QUERY, executionAccess=access, session=conversation)
    assert results[0].rawText == "ok" and len(other[4].opens) == 1
