# file: tests/backend/runtime/test_executionResources.py ; version: 1
"""Deterministic neutral admission/lifetime contracts using only fake callbacks."""
from __future__ import annotations

from concurrent.futures import Future
from threading import Barrier, Event, Thread

import pytest

from backend.orchestration.cancellation import CancellationSignal
from backend.runtime.executionResources import (
    ConflictReason, EffectStatus, ExecutionResourceDomain, ExecutionResourceService,
    Materialization, OperationClaim, OwnerReservation, ProfileReference, ProviderEffect,
    Quiescence, ResidencyDescriptor, ResourceCleanupError, ResourceConflict,
    ResourceParticipant, ResourceState, RetainedContextPin,
)

OK = ProviderEffect(EffectStatus.ACKNOWLEDGED, Quiescence.CONFIRMED)
UNKNOWN = ProviderEffect(EffectStatus.UNKNOWN, Quiescence.UNKNOWN)
FAILED_EMPTY = ProviderEffect(EffectStatus.FAILED, Quiescence.CONFIRMED)


class _PrivatePayload:
    """Fails if the core renders, hashes or compares provider-private objects."""

    def __repr__(self):
        """Makes accidental diagnostic disclosure fail the test."""
        raise AssertionError("private repr")

    def __eq__(self, other):
        """Detects opaque equality under any lock or diagnostic path."""
        raise AssertionError("private equality")

    def __hash__(self):
        """Detects opaque hashing instead of exact immutable identity bytes."""
        raise AssertionError("private hash")


class _Adapter:
    """Records exact generation calls and permits deterministic callback control."""

    def __init__(self):
        """Creates an acknowledged fake with no external resources."""
        self.loads = []
        self.releases = []
        self.onLoad = None
        self.onRelease = None

    def materialize(self, descriptor, token):
        """Records the attempt before running an optional test callback."""
        self.loads.append(token)
        if self.onLoad:
            return self.onLoad(descriptor, token)
        return Materialization(OK, True, _PrivatePayload())

    def release(self, token, payload):
        """Records disposal once per actual callback, with private payload intact."""
        self.releases.append(token)
        if self.onRelease:
            return self.onRelease(token, payload)
        return OK


def _fixture(*, loaded=True):
    """Creates two approved profiles and independently granted host participants."""
    adapter = _Adapter()
    service = ExecutionResourceService()
    domain = service.registerDomain("test.resource", compatibilityKey=b"envelope", adapter=adapter)
    first = domain.registerProfile(ResidencyDescriptor(b"A", _PrivatePayload()))
    second = domain.registerProfile(ResidencyDescriptor(b"B", _PrivatePayload()))
    owner = domain.openParticipant(allowedProfiles=(first, second), allowIdleTransition=True)
    peer = domain.openParticipant(allowedProfiles=(first, second), allowIdleTransition=True)
    if loaded:
        assert owner.transitionIdle(first).snapshot.state is ResourceState.READY
    return service, domain, adapter, first, second, owner, peer


def _background(callback):
    """Captures thread exceptions in a Future; daemon status bounds broken tests."""
    future = Future()

    def invoke():
        """Runs one concurrent operation with its real exception/result retained."""
        try:
            future.set_result(callback())
        except BaseException as error:
            future.set_exception(error)

    Thread(target=invoke, daemon=True).start()
    return future


def _deny(result, reason):
    """Checks exact bounded rejection rather than exception text or timing."""
    assert isinstance(result, ResourceConflict)
    assert result.reason is reason


def _raise(*args):
    """Simulates a private provider exception whose text must never escape."""
    raise RuntimeError("private credentials and payload")


def test_explicit_initial_transition_and_exact_grants():
    """Participant lifetime alone cannot load, authorize or pin a resource."""
    _, domain, adapter, first, second, owner, _ = _fixture(loaded=False)
    readonly = domain.openParticipant(allowedProfiles=(first,))
    _deny(readonly.acquire(first), ConflictReason.NOT_RESIDENT)
    _deny(readonly.transitionIdle(first), ConflictReason.UNAUTHORIZED)
    _deny(readonly.acquire(second), ConflictReason.UNAUTHORIZED)
    assert adapter.loads == []
    owner.transitionIdle(first)
    readonly.close()
    assert owner.transitionIdle(second).snapshot.state is ResourceState.READY
    assert len(adapter.loads) == 2


def test_private_payload_registration_and_diagnostics():
    """Repeated profile registration requires exact descriptor issuance, never equality."""
    _, domain, _, first, _, owner, _ = _fixture()
    assert domain.registerProfile(first._descriptor) is first
    _deny(domain.registerProfile(ResidencyDescriptor(b"A", _PrivatePayload())), ConflictReason.INVALID_HANDLE)
    _deny(owner.acquire(ProfileReference(first._descriptor)), ConflictReason.UNAUTHORIZED)
    forged = ResourceParticipant(domain, frozenset((first,)), True)
    _deny(forged.acquire(first), ConflictReason.INVALID_HANDLE)
    assert "providerPayload" not in repr(first._descriptor)
    assert "private" not in repr(domain.snapshot)


def test_compatible_claims_block_switch_and_duplicate_finish_is_idempotent():
    """Both owners share a generation without permitting active replacement."""
    _, domain, adapter, first, second, owner, peer = _fixture()
    a, b = owner.acquire(first), peer.acquire(first)
    assert isinstance(a, OperationClaim) and isinstance(b, OperationClaim)
    assert a.token is b.token
    _deny(owner.transitionIdle(second), ConflictReason.BUSY)
    assert adapter.releases == []
    a.finish(OK)
    assert a.finish(OK).duplicate
    _deny(a.finish(UNKNOWN), ConflictReason.INVALID_HANDLE)
    _deny(owner.transitionIdle(second), ConflictReason.BUSY)
    b.finish(OK)
    assert owner.transitionIdle(second).snapshot.state is ResourceState.READY
    assert len(adapter.releases) == 1
    assert domain.snapshot.operations == 0


def test_barrier_admission_vs_transition_has_one_winner():
    """Simultaneous admission/switch cannot stop any protected operation."""
    _, domain, adapter, first, second, owner, peer = _fixture()
    barrier = Barrier(3)

    def admit():
        """Competes at the same deterministic starting barrier."""
        barrier.wait(timeout=5)
        return peer.acquire(first)

    def switch():
        """Attempts a single immediate transition without retry policy."""
        barrier.wait(timeout=5)
        return owner.transitionIdle(second)

    admission, transition = _background(admit), _background(switch)
    barrier.wait(timeout=5)
    claim, result = admission.result(5), transition.result(5)
    if isinstance(claim, OperationClaim):
        _deny(result, ConflictReason.BUSY)
        assert adapter.releases == []
        assert domain.snapshot.residency is claim.token
        claim.finish(OK)
    else:
        assert claim.reason in (ConflictReason.TRANSITIONING, ConflictReason.NOT_RESIDENT)
        assert result.snapshot.state is ResourceState.READY
        assert len(adapter.releases) == 1


def test_transition_callback_blocks_other_transition_and_admission():
    """A reserved ticket remains authoritative while materialization is blocked."""
    _, domain, adapter, first, second, owner, peer = _fixture()
    entered, release = Event(), Event()

    def load(descriptor, token):
        """Holds readiness while competitors inspect the transition gate."""
        entered.set()
        assert release.wait(5)
        return Materialization(OK, True, _PrivatePayload())

    adapter.onLoad = load
    pending = _background(lambda: owner.transitionIdle(second))
    try:
        assert entered.wait(5)
        _deny(peer.acquire(first), ConflictReason.TRANSITIONING)
        _deny(peer.transitionIdle(first), ConflictReason.TRANSITIONING)
    finally:
        release.set()
    assert pending.result(5).snapshot.state is ResourceState.READY
    assert domain.snapshot.residency.generation == 2


def test_two_exclusive_requests_and_nonreentrant_calls():
    """Only one exclusive owner wins; even unrelated compatible calls are denied."""
    _, domain, _, first, _, owner, peer = _fixture()
    barrier = Barrier(3)

    def reserve(participant):
        """Synchronizes competing exclusive requests without sleep assumptions."""
        barrier.wait(timeout=5)
        return participant.reserveExclusive(first)

    a = _background(lambda: reserve(owner))
    b = _background(lambda: reserve(peer))
    barrier.wait(timeout=5)
    results = (a.result(5), b.result(5))
    assert sum(isinstance(x, OwnerReservation) for x in results) == 1
    reservation = next(x for x in results if isinstance(x, OwnerReservation))
    _deny(next(x for x in results if isinstance(x, ResourceConflict)), ConflictReason.BUSY)
    winner = reservation._owner
    loser = peer if winner is owner else owner
    _deny(loser.acquire(first), ConflictReason.BUSY)
    _deny(winner.acquire(first), ConflictReason.BUSY)
    _deny(loser.acquire(first, reservation=reservation), ConflictReason.INVALID_HANDLE)
    claim = winner.acquire(first, reservation=reservation)
    _deny(winner.acquire(first, reservation=reservation), ConflictReason.BUSY)
    claim.finish(OK)
    assert domain.snapshot.exclusive
    reservation.close()
    assert not domain.snapshot.exclusive


def test_reservation_vs_compatible_active_operation():
    """An active compatible operation blocks new exclusivity without preemption."""
    _, _, _, first, _, owner, peer = _fixture()
    claim = owner.acquire(first)
    _deny(peer.reserveExclusive(first), ConflictReason.BUSY)
    claim.finish(OK)
    assert isinstance(peer.reserveExclusive(first), OwnerReservation)


@pytest.mark.parametrize("closeKind", ("owner", "reservation", "domain"))
@pytest.mark.parametrize("effect", (OK, UNKNOWN))
def test_revocation_preserves_late_cleanup_authority(closeKind, effect):
    """Closed use remains invalid while exact issued claims can finish late."""
    _, domain, adapter, first, second, owner, peer = _fixture()
    reservation = owner.reserveExclusive(first)
    claim = owner.acquire(first, reservation=reservation)
    stops = []
    claim.bindStop(lambda: stops.append("stop"))
    if closeKind == "owner":
        owner.close()
    elif closeKind == "reservation":
        reservation.close()
    else:
        domain.close()
        assert stops == []  # Stage one invokes no callbacks.
        domain.finishClose()
    assert not claim.usable
    assert stops == ["stop"]
    assert adapter.releases == []
    result = claim.finish(effect)
    assert result.effect == effect
    assert claim.finish(effect).duplicate
    if effect is UNKNOWN:
        assert domain.snapshot.state is ResourceState.UNKNOWN
        assert domain.snapshot.operations == 1
    elif closeKind == "domain":
        assert domain.finishClose().snapshot.state is ResourceState.CLOSED
        assert len(adapter.releases) == 1
    else:
        assert domain.snapshot.operations == 0
        assert peer.transitionIdle(second).snapshot.state is ResourceState.READY


def test_retained_pin_between_calls_blocks_switch_and_foreign_use():
    """Required reuse retains a generation even when no operation is active."""
    _, domain, adapter, first, second, owner, peer = _fixture()
    disposed = []
    claim = owner.acquire(first)
    pin = claim.retain(_PrivatePayload(), lambda payload: disposed.append("dispose") or OK)
    claim.finish(OK)
    assert domain.snapshot.operations == 0 and domain.snapshot.retainedContexts == 1
    _deny(peer.transitionIdle(second), ConflictReason.BUSY)
    _deny(peer.reserveExclusive(first), ConflictReason.BUSY)
    _deny(peer.acquire(first, retained=pin), ConflictReason.INVALID_HANDLE)
    reuse = owner.acquire(first, retained=pin)
    _deny(owner.acquire(first, retained=pin), ConflictReason.BUSY)
    reuse.finish(OK)
    assert len(adapter.loads) == 1
    pin.close()
    assert pin.close().duplicate
    assert disposed == ["dispose"]
    _deny(owner.acquire(first, retained=pin), ConflictReason.CONTEXT_LOST)
    assert owner.transitionIdle(second).snapshot.state is ResourceState.READY


@pytest.mark.parametrize("closeKind", ("pin", "owner", "reservation", "domain"))
def test_deferred_pin_disposer_has_owner_after_close_during_use(closeKind):
    """Revocation never orphans the registered disposer or releases during use."""
    _, domain, adapter, first, second, owner, peer = _fixture()
    reservation = owner.reserveExclusive(first)
    claim = owner.acquire(first, reservation=reservation)
    disposed = []
    pin = claim.retain(_PrivatePayload(), lambda payload: disposed.append("dispose") or OK)
    if closeKind == "pin":
        pin.close()
    elif closeKind == "owner":
        owner.close()
    elif closeKind == "reservation":
        reservation.close()
    else:
        domain.close()
    assert disposed == []
    assert domain.snapshot.exclusive
    denied = peer.transitionIdle(second)
    assert isinstance(denied, ResourceConflict)
    assert denied.reason in (ConflictReason.DRAINING, ConflictReason.CLOSED)
    assert adapter.releases == []
    claim.finish(OK)
    if closeKind == "domain":
        assert disposed == []
        domain.finishClose()
    assert disposed == ["dispose"]
    assert domain.snapshot.retainedContexts == 0
    pin.close()
    assert disposed == ["dispose"]
    if closeKind == "pin":
        reservation.close()
    if closeKind == "domain":
        domain.finishClose()
    else:
        assert not domain.snapshot.exclusive


@pytest.mark.parametrize("callback", (_raise, lambda payload: UNKNOWN, lambda payload: FAILED_EMPTY))
def test_failed_pin_disposal_keeps_proper_blocker_and_never_repeats(callback):
    """Disposer exception/uncertainty cannot become idle residency."""
    _, domain, _, first, second, owner, _ = _fixture()
    calls = []

    def dispose(payload):
        """Counts attempts even when the injected provider action fails."""
        calls.append(1)
        return callback(payload)

    claim = owner.acquire(first)
    pin = claim.retain(_PrivatePayload(), dispose)
    claim.finish(OK)
    pin.close()
    pin.close()
    owner.close()
    domain.finishClose()
    assert calls == [1]
    assert domain.snapshot.retainedContexts == 1
    assert domain.snapshot.state in (ResourceState.FAILED, ResourceState.UNKNOWN)


def test_disposer_callback_can_enter_domain_from_another_thread():
    """Second-thread access proves absence of held locks during disposal."""
    _, domain, _, first, _, owner, _ = _fixture()
    observed = []

    def dispose(payload):
        """Requires a different thread to enter the domain and close the same pin."""
        observed.append(_background(lambda: (domain.snapshot, pin.close())).result(5))
        return OK

    claim = owner.acquire(first)
    pin = claim.retain(_PrivatePayload(), dispose)
    claim.finish(OK)
    pin.close()
    assert len(observed) == 1
    assert domain.snapshot.retainedContexts == 0


def test_context_loss_and_stale_callbacks_do_not_affect_new_generations():
    """Lost required reuse fails; stale cleanup cannot clear replacement state."""
    _, domain, _, first, second, owner, _ = _fixture()
    old = owner.acquire(first)
    pin = old.retain(None, lambda payload: OK)
    old.finish(OK)
    pin.invalidate()
    _deny(owner.acquire(first, retained=pin), ConflictReason.CONTEXT_LOST)
    owner.transitionIdle(second)
    owner.transitionIdle(first)
    new = owner.acquire(first)
    assert new.token.generation > old.token.generation
    assert old.finish(OK).duplicate
    pin.invalidate()
    _deny(old.finish(UNKNOWN), ConflictReason.INVALID_HANDLE)
    assert domain.snapshot.residency is new.token
    assert domain.snapshot.operations == 1
    new.finish(OK)


@pytest.mark.parametrize("effect", (UNKNOWN, FAILED_EMPTY))
def test_unknown_or_failed_release_never_loads_replacement(effect):
    """Release evidence must explicitly permit a new materialization."""
    _, domain, adapter, first, second, owner, _ = _fixture()
    adapter.onRelease = lambda token, payload: effect
    result = owner.transitionIdle(second)
    assert result.snapshot.state in (ResourceState.FAILED, ResourceState.UNKNOWN)
    assert len(adapter.loads) == 1
    domain.finishClose()
    domain.finishClose()
    assert len(adapter.releases) == 1


def test_release_exception_is_unknown_without_private_diagnostics():
    """A provider release exception does not claim old residency was removed."""
    _, domain, adapter, _, second, owner, _ = _fixture()
    adapter.onRelease = _raise
    result = owner.transitionIdle(second)
    assert result.snapshot.state is ResourceState.UNKNOWN
    assert "private" not in repr(result)
    _deny(owner.acquire(second), ConflictReason.UNKNOWN_QUIESCENCE)


def test_failed_known_empty_startup_requires_explicit_retry_with_new_generation():
    """Confirmed-empty failure permits explicit retry, never an automatic one."""
    _, domain, adapter, first, _, owner, _ = _fixture(loaded=False)
    adapter.onLoad = lambda descriptor, token: Materialization(FAILED_EMPTY, False)
    assert owner.transitionIdle(first).snapshot.state is ResourceState.FAILED
    _deny(owner.acquire(first), ConflictReason.FAILED)
    assert len(adapter.loads) == 1
    adapter.onLoad = None
    assert owner.transitionIdle(first).snapshot.state is ResourceState.READY
    assert [token.generation for token in adapter.loads] == [1, 2]


@pytest.mark.parametrize("callback", (_raise, lambda descriptor, token: Materialization(UNKNOWN, False), lambda descriptor, token: None))
def test_load_exception_or_unknown_never_allows_retry(callback):
    """Missing startup quiescence evidence creates a non-recoverable ER-01 tombstone."""
    _, domain, adapter, first, _, owner, _ = _fixture(loaded=False)
    adapter.onLoad = callback
    assert owner.transitionIdle(first).snapshot.state is ResourceState.UNKNOWN
    _deny(owner.transitionIdle(first), ConflictReason.UNKNOWN_QUIESCENCE)
    assert len(adapter.loads) == 1


@pytest.mark.parametrize("closeKind", ("cancel", "owner", "domain"))
@pytest.mark.parametrize("releaseEffect", (OK, UNKNOWN))
def test_late_materialization_cleanup_after_revocation(closeKind, releaseEffect):
    """Unpublished provider objects are disposed exactly once on their own ticket."""
    _, domain, adapter, first, _, owner, _ = _fixture(loaded=False)
    entered, proceed = Event(), Event()
    cancellation = CancellationSignal()

    def load(descriptor, token):
        """Returns an object after revocation has deterministically won admission."""
        entered.set()
        assert proceed.wait(5)
        return Materialization(OK, True, _PrivatePayload())

    adapter.onLoad = load
    adapter.onRelease = lambda token, payload: releaseEffect
    pending = _background(lambda: owner.transitionIdle(first, cancellation=cancellation))
    try:
        assert entered.wait(5)
        if closeKind == "cancel":
            cancellation.request()
        elif closeKind == "owner":
            owner.close()
        else:
            domain.close()
        assert domain.snapshot.residency is None
    finally:
        proceed.set()
    result = pending.result(5)
    assert result.cancelled
    assert len(adapter.releases) == 1
    expected = ResourceState.UNKNOWN if releaseEffect is UNKNOWN else (
        ResourceState.CLOSED if closeKind == "domain" else ResourceState.EMPTY)
    assert result.snapshot.state is expected
    if closeKind == "domain":
        domain.finishClose()
        assert len(adapter.releases) == 1


def test_cancel_before_transition_or_admission_calls_no_adapter():
    """Already requested cancellation cannot leak an operation or start a load."""
    _, domain, adapter, first, _, owner, _ = _fixture(loaded=False)
    cancellation = CancellationSignal()
    cancellation.request()
    _deny(owner.transitionIdle(first, cancellation=cancellation), ConflictReason.CANCELLED)
    assert adapter.loads == []
    owner.transitionIdle(first)
    _deny(owner.acquire(first, cancellation=cancellation), ConflictReason.CANCELLED)
    assert domain.snapshot.operations == 0


class _CancelOnRegistration(CancellationSignal):
    """Places cancellation exactly between admission and callback registration."""

    def registerCallback(self, callback):
        """Exercises synchronous already-cancelled callback delivery."""
        self.request()
        return super().registerCallback(callback)


def test_cancel_during_registration_releases_unpublished_claim():
    """A claim never delivered to its adapter has no provider work to drain."""
    _, domain, _, first, _, owner, _ = _fixture()
    _deny(owner.acquire(first, cancellation=_CancelOnRegistration()), ConflictReason.CANCELLED)
    assert domain.snapshot.operations == 0
    assert domain.snapshot.state is ResourceState.READY


@pytest.mark.parametrize("closeKind", ("cancel", "owner"))
def test_late_stop_binding_delivers_once_and_replacement_is_rejected(closeKind):
    """A pending stop survives absent callbacks, with explicit one-binding semantics."""
    _, _, _, first, _, owner, _ = _fixture()
    cancellation = CancellationSignal()
    claim = owner.acquire(first, cancellation=cancellation)
    if closeKind == "cancel":
        cancellation.request()
    else:
        owner.close()
    calls = []
    unregister = claim.bindStop(lambda: calls.append(1))
    assert calls == [1]
    _deny(claim.bindStop(lambda: None), ConflictReason.INVALID_HANDLE)
    unregister()
    claim.requestStop()
    assert calls == [1]
    claim.finish(OK)
    assert claim.finish(OK).duplicate


def test_stop_exception_is_contained_and_does_not_confirm_quiescence():
    """Stop failure is typed separately from eventual confirmed execution finish."""
    _, domain, _, first, _, owner, _ = _fixture()
    claim = owner.acquire(first)
    claim.bindStop(_raise)
    result = claim.requestStop()
    assert result.effect.status is EffectStatus.FAILED
    assert result.effect.quiescence is Quiescence.UNKNOWN
    assert domain.snapshot.operations == 1
    claim.finish(UNKNOWN)
    assert domain.snapshot.state is ResourceState.UNKNOWN


def test_confirmed_finish_does_not_race_still_running_stop_callback():
    """A late stop callback cannot run against a replacement generation."""
    _, domain, adapter, first, second, owner, peer = _fixture()
    entered, proceed = Event(), Event()
    claim = owner.acquire(first)

    def stop():
        """Blocks after dispatch so finish can race before the stop returns."""
        entered.set()
        assert proceed.wait(5)

    claim.bindStop(stop)
    pending = _background(claim.requestStop)
    try:
        assert entered.wait(5)
        claim.finish(OK)
        assert domain.snapshot.operations == 1
        _deny(peer.transitionIdle(second), ConflictReason.DRAINING)
        assert adapter.releases == []
    finally:
        proceed.set()
    pending.result(5)
    assert domain.snapshot.operations == 0
    assert peer.transitionIdle(second).snapshot.state is ResourceState.READY


def test_service_tombstone_and_foreign_generation_releases():
    """Final owner close cannot mint a replacement domain or release foreign claims."""
    service, domain, adapter, first, _, owner, _ = _fixture()
    claim = owner.acquire(first)
    owner.close()
    claim.finish(UNKNOWN)
    assert service.registerDomain("test.resource", compatibilityKey=b"envelope", adapter=adapter) is domain
    with pytest.raises(RuntimeError):
        service.registerDomain("test.resource", compatibilityKey=b"other", adapter=adapter)
    foreign = ExecutionResourceDomain("foreign", adapter)
    _deny(foreign._finish(claim, OK), ConflictReason.INVALID_HANDLE)
    forged = OperationClaim(domain, owner, claim.token, None, None)
    _deny(forged.finish(OK), ConflictReason.INVALID_HANDLE)
    with pytest.raises(ResourceCleanupError):
        service.finishClose()
    with pytest.raises(ResourceCleanupError):
        service.finishClose()
    with pytest.raises(RuntimeError):
        service.registerDomain("test.resource", compatibilityKey=b"envelope", adapter=adapter)
    assert adapter.releases == []


def test_idle_fresh_operations_reuse_weights_without_creating_context_state():
    """Model reuse does not imply inference context retention or a reset guarantee."""
    _, domain, adapter, first, _, owner, _ = _fixture()
    for _ in range(3):
        claim = owner.acquire(first)
        claim.finish(OK)
    assert len(adapter.loads) == 1
    assert adapter.releases == []
    assert domain.snapshot.retainedContexts == 0


def test_idle_unload_and_repeated_close_dispose_once():
    """Explicit unload has the same authority and exactly-once release rules."""
    service, domain, adapter, first, _, owner, _ = _fixture()
    assert owner.unloadIdle().snapshot.state is ResourceState.EMPTY
    assert len(adapter.releases) == 1
    owner.transitionIdle(first)
    service.close()
    assert len(adapter.releases) == 1
    service.finishClose()
    service.finishClose()
    assert len(adapter.releases) == 2
    assert domain.snapshot.state is ResourceState.CLOSED


def test_failed_materialized_object_reports_load_and_disposal_separately():
    """Successful disposal cannot relabel failed readiness as a successful load."""
    _, domain, adapter, first, _, owner, _ = _fixture(loaded=False)
    adapter.onLoad = lambda descriptor, token: Materialization(UNKNOWN, True, _PrivatePayload())
    result = owner.transitionIdle(first)
    assert result.materializationEffect is UNKNOWN
    assert result.effect == OK
    assert result.snapshot.state is ResourceState.EMPTY
    assert len(adapter.releases) == 1 and len(adapter.loads) == 1
    _deny(owner.acquire(first), ConflictReason.NOT_RESIDENT)


def test_cancellation_at_transition_registration_does_not_unload_existing_residency():
    """Revocation before any provider callback leaves the old owned residency intact."""
    _, domain, adapter, first, second, owner, _ = _fixture()
    token = domain.snapshot.residency
    result = owner.transitionIdle(second, cancellation=_CancelOnRegistration())
    assert result.cancelled
    assert domain.snapshot.residency is token
    assert adapter.releases == [] and len(adapter.loads) == 1


def test_cancel_during_old_release_cleans_to_empty_without_loading_new_profile():
    """Confirmed release is not undone or followed by load after cancellation wins."""
    _, domain, adapter, _, second, owner, _ = _fixture()
    entered, proceed = Event(), Event()
    cancellation = CancellationSignal()

    def release(token, payload):
        """Pauses the old object's cleanup to place revocation before new load."""
        entered.set()
        assert proceed.wait(5)
        return OK

    adapter.onRelease = release
    pending = _background(lambda: owner.transitionIdle(second, cancellation=cancellation))
    try:
        assert entered.wait(5)
        cancellation.request()
    finally:
        proceed.set()
    result = pending.result(5)
    assert result.cancelled and result.snapshot.state is ResourceState.EMPTY
    assert len(adapter.loads) == 1 and len(adapter.releases) == 1


class _CaptureCancellation(CancellationSignal):
    """Retains already-unregistered callbacks to model a stale external delivery."""

    def __init__(self):
        """Tracks callbacks without changing normal signal request behavior."""
        super().__init__()
        self.callbacks = []

    def registerCallback(self, callback):
        """Keeps an external copy even after normal cancellation unregistration."""
        self.callbacks.append(callback)
        return super().registerCallback(callback)


def test_stale_transition_cancellation_cannot_revoke_new_generation():
    """Ticket identity defeats late cancellation after a later profile is resident."""
    _, domain, adapter, first, second, owner, _ = _fixture(loaded=False)
    cancellation = _CaptureCancellation()
    owner.transitionIdle(first, cancellation=cancellation)
    owner.transitionIdle(second)
    token = domain.snapshot.residency
    cancellation.callbacks[0]()
    assert domain.snapshot.residency is token and domain.snapshot.state is ResourceState.READY
    assert len(adapter.loads) == 2 and len(adapter.releases) == 1


def test_unknown_operation_evidence_is_not_downgraded_by_late_disposer_failure():
    """A confirmed-but-failed context disposal cannot erase another unknown blocker."""
    _, domain, _, first, _, owner, peer = _fixture()
    entered, proceed = Event(), Event()

    def dispose(payload):
        """Returns a bounded failure after another operation becomes unknown."""
        entered.set()
        assert proceed.wait(5)
        return FAILED_EMPTY

    initial = owner.acquire(first)
    pin = initial.retain(object(), dispose)
    initial.finish(OK)
    other = peer.acquire(first)
    pending = _background(pin.close)
    try:
        assert entered.wait(5)
        other.finish(UNKNOWN)
    finally:
        proceed.set()
    pending.result(5)
    assert domain.snapshot.state is ResourceState.UNKNOWN
    assert domain.snapshot.retainedContexts == 1 and domain.snapshot.operations == 1


def test_forged_closed_reservation_cannot_claim_idempotent_disposal():
    """Only the issued reservation receives duplicate-cleanup semantics."""
    _, domain, _, first, _, owner, _ = _fixture()
    issued = owner.reserveExclusive(first)
    forged = OwnerReservation(domain, owner, issued.token)
    forged._closed = True
    _deny(forged.close(), ConflictReason.INVALID_HANDLE)
    assert domain.snapshot.exclusive
    issued.close()
    assert issued.close().duplicate


def test_stale_pin_disposer_completion_does_not_remove_another_context():
    """Deferred disposal publishes only for its pin while another pin stays protected."""
    _, domain, adapter, first, second, owner, peer = _fixture()
    entered, proceed = Event(), Event()

    def dispose(payload):
        """Keeps the old pin's exact cleanup ticket in flight."""
        entered.set()
        assert proceed.wait(5)
        return OK

    claim = owner.acquire(first)
    oldPin = claim.retain(object(), dispose)
    newPin = claim.retain(object(), lambda payload: OK)
    claim.finish(OK)
    pending = _background(oldPin.close)
    try:
        assert entered.wait(5)
        assert domain.snapshot.retainedContexts == 2
        _deny(peer.transitionIdle(second), ConflictReason.DRAINING)
    finally:
        proceed.set()
    pending.result(5)
    assert domain.snapshot.retainedContexts == 1
    assert len(adapter.loads) == 1 and adapter.releases == []
    reuse = owner.acquire(first, retained=newPin)
    assert isinstance(reuse, OperationClaim)
    reuse.finish(OK)
    oldPin.close()
    assert domain.snapshot.retainedContexts == 1


class _BrokenRegistration(CancellationSignal):
    """Simulates a callback-registration error after the callback was installed."""

    def registerCallback(self, callback):
        """Fails while leaving a stale callback copy in the signal."""
        super().registerCallback(callback)
        raise RuntimeError("private registration failure")


def test_registration_exception_cannot_orphan_unpublished_admission_or_transition():
    """Failed registration leaves no work or transition ticket despite stale delivery."""
    _, domain, adapter, first, second, owner, _ = _fixture()
    cancellation = _BrokenRegistration()
    _deny(owner.acquire(first, cancellation=cancellation), ConflictReason.FAILED)
    _deny(owner.transitionIdle(second, cancellation=cancellation), ConflictReason.FAILED)
    cancellation.request()
    assert domain.snapshot.state is ResourceState.READY
    assert domain.snapshot.operations == 0 and len(adapter.loads) == 1
    assert adapter.releases == []


def test_unknown_peer_work_revokes_validity_for_new_provider_entry():
    """Already issued claims keep cleanup rights but cannot start reuse after unknown."""
    _, domain, _, first, _, owner, peer = _fixture()
    old, waiting = owner.acquire(first), peer.acquire(first)
    assert waiting.usable
    old.finish(UNKNOWN)
    assert not waiting.usable
    waiting.finish(OK)
    assert domain.snapshot.state is ResourceState.UNKNOWN
    assert domain.snapshot.operations == 1
