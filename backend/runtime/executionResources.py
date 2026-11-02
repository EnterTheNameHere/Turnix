# file: backend/runtime/executionResources.py ; version: 1
"""Host-owned admission and cleanup for one provider-neutral residency per domain.

Composition and adapter hooks are trusted backend APIs, not Pack authority. The
service retains domain tombstones for its entire lifetime. No callbacks execute
under its locks. This module supplies neither provider adoption nor recovery of
unknown quiescence, scheduling, inference contexts or global hardware exclusivity.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
from typing import Protocol
from weakref import WeakSet

from backend.orchestration.cancellation import CancellationSignal

__all__ = [
    "ConflictReason", "EffectStatus", "ExecutionResourceDomain", "ExecutionResourceService",
    "Materialization", "OperationClaim", "OwnerReservation", "ProfileReference", "ProviderEffect",
    "Quiescence", "ReleaseOutcome", "ResidencyAdapter", "ResidencyDescriptor", "ResidencyToken",
    "ResourceCleanupError", "ResourceConflict", "ResourceParticipant", "ResourceSnapshot",
    "ResourceState", "RetainedContextPin", "TransitionOutcome",
]


class ResourceState(StrEnum):
    """Admission disposition, independent of any Job terminal outcome."""

    EMPTY = "empty"
    READY = "ready"
    TRANSITIONING = "transitioning"
    DRAINING = "draining"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CLOSED = "closed"


class ConflictReason(StrEnum):
    """Bounded operational rejection codes without provider-private details."""

    BUSY = "busy"
    TRANSITIONING = "transitioning"
    DRAINING = "draining"
    NOT_RESIDENT = "not-resident"
    UNKNOWN_QUIESCENCE = "unknown-quiescence"
    FAILED = "failed"
    CLOSED = "closed"
    UNAUTHORIZED = "unauthorized"
    INVALID_HANDLE = "invalid-handle"
    CONTEXT_LOST = "context-lost"
    CANCELLED = "cancelled"


class EffectStatus(StrEnum):
    """What the trusted adapter reports about a specific attempted action."""

    ATTEMPTED = "attempted"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    UNKNOWN = "unknown"


class Quiescence(StrEnum):
    """Whether the adapter confirms the owned work has ceased."""

    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderEffect:
    """Trusted evidence, never an owner assertion or a hidden-state guarantee.

    ACKNOWLEDGED plus CONFIRMED on release means the particular owned resource was
    disposed. CONFIRMED on operation finish means its provider execution has ceased.
    Exceptions and malformed callback results become FAILED/UNKNOWN without exposing
    exception text. No arbitrary diagnostic strings are accepted.
    """

    status: EffectStatus
    quiescence: Quiescence

    def __post_init__(self) -> None:
        """Rejects values outside the small, bounded lifecycle vocabulary."""
        if type(self.status) is not EffectStatus or type(self.quiescence) is not Quiescence:
            raise TypeError("Expected typed provider effect and quiescence.")


_CONFIRMED = ProviderEffect(EffectStatus.ACKNOWLEDGED, Quiescence.CONFIRMED)
_UNCERTAIN = ProviderEffect(EffectStatus.FAILED, Quiescence.UNKNOWN)


@dataclass(frozen=True, slots=True, eq=False)
class ResidencyDescriptor:
    """Adapter-supplied complete immutable identity and private immutable payload.

    The adapter owns payload immutability and completeness of identity. Identity must
    be an opaque safe-to-disclose fingerprint, not raw private launch configuration.
    The core
    never compares, hashes, renders or introspects providerPayload. Registering the
    same identity with another descriptor is a conflict, even if payloads look equal.
    """

    identity: bytes
    providerPayload: object = field(repr=False)

    def __post_init__(self) -> None:
        """Requires a nonempty exact byte identity with safe core equality."""
        if type(self.identity) is not bytes or not self.identity:
            raise ValueError("Residency identity must be nonempty exact bytes.")


@dataclass(frozen=True, slots=True)
class ResidencyToken:
    """Immutable evidence for one attempt; a token alone conveys no authority."""

    domainNonce: object = field(repr=False)
    generation: int
    identity: bytes


@dataclass(frozen=True, slots=True)
class Materialization:
    """Adapter result whose present object requires release even on failed load.

    present=False with confirmed quiescence certifies no owned object remains.
    An exception supplies no such evidence and quarantines the attempt. An object
    may be published only for present=True and acknowledged/confirmed readiness.
    """

    effect: ProviderEffect
    present: bool
    providerPayload: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Validates core result structure without touching opaque payloads."""
        if type(self.effect) is not ProviderEffect or type(self.present) is not bool:
            raise TypeError("Expected typed materialization result.")


class ResidencyAdapter(Protocol):
    """Trusted synchronous callbacks; all provider effects happen outside locks."""

    def materialize(self, descriptor: ResidencyDescriptor, token: ResidencyToken) -> Materialization:
        """Returns readiness or truthful failure evidence for this exact attempt."""
        ...

    def release(self, token: ResidencyToken, providerPayload: object) -> ProviderEffect:
        """Disposes this exact owned object, reporting release and quiescence."""
        ...


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """Safe counts and residency evidence, excluding owner labels and payloads."""

    state: ResourceState
    residency: ResidencyToken | None
    operations: int
    retainedContexts: int
    exclusive: bool


@dataclass(frozen=True, slots=True)
class ResourceConflict:
    """Immediate denial with bounded contention evidence; no implicit retry."""

    reason: ConflictReason
    snapshot: ResourceSnapshot


@dataclass(frozen=True, slots=True)
class ReleaseOutcome:
    """Idempotent cleanup disposition, which can remain blocked after disposal."""

    snapshot: ResourceSnapshot
    effect: ProviderEffect | None = None
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class TransitionOutcome:
    """Separates caller admission cancellation from resource cleanup disposition."""

    snapshot: ResourceSnapshot
    effect: ProviderEffect | None
    cancelled: bool = False
    materializationEffect: ProviderEffect | None = None


class ResourceCleanupError(RuntimeError):
    """Signals incomplete shutdown without leaking provider errors or payloads."""

    def __init__(self, snapshots: tuple[ResourceSnapshot, ...]) -> None:
        """Retains safe evidence so repeated shutdown can report unresolved work."""
        super().__init__("Execution resources have pending, failed or unknown cleanup.")
        self.snapshots = snapshots


class ProfileReference:
    """Opaque host-issued profile capability; only exact registered objects work."""

    def __init__(self, descriptor: ResidencyDescriptor) -> None:
        """Stores private immutable adapter configuration for host registration."""
        self._descriptor = descriptor


class ResourceParticipant:
    """Trusted host-created owner grant; lifetime alone does not pin residency."""

    def __init__(self, domain: ExecutionResourceDomain, profiles: frozenset[ProfileReference], transition: bool) -> None:
        """Binds immutable grants; direct construction does not issue authority."""
        self._domain = domain
        self._profiles = profiles
        self._transition = transition
        self._closed = False

    def acquire(self, profile: ProfileReference, *, reservation: OwnerReservation | None = None,
                retained: RetainedContextPin | None = None,
                cancellation: CancellationSignal | None = None) -> OperationClaim | ResourceConflict:
        """Admits compatible work immediately; never loads or switches residency."""
        return self._domain._acquire(self, profile, reservation, retained, cancellation)

    def reserveExclusive(self, profile: ProfileReference) -> OwnerReservation | ResourceConflict:
        """Pins a ready generation and excludes unrelated compatible work too."""
        return self._domain._reserve(self, profile)

    def transitionIdle(self, profile: ProfileReference, *, cancellation: CancellationSignal | None = None
                       ) -> TransitionOutcome | ResourceConflict:
        """Explicitly requests a host-authorized initial load or idle replacement."""
        return self._domain._transition(self, profile, cancellation)

    def unloadIdle(self) -> TransitionOutcome | ResourceConflict:
        """Requests authorized release only when all claims and pins are absent."""
        return self._domain._transition(self, None, None)

    def close(self) -> ReleaseOutcome | ResourceConflict:
        """Revokes new use, requests owned stops and preserves late cleanup rights."""
        return self._domain._closeParticipant(self)


class OwnerReservation:
    """Exclusive owner capability; close cannot release outstanding child work."""

    def __init__(self, domain: ExecutionResourceDomain, owner: ResourceParticipant, token: ResidencyToken) -> None:
        """Binds one issued reservation to its owner and immutable residency."""
        self._domain, self._owner, self.token = domain, owner, token
        self._closed = False

    def close(self) -> ReleaseOutcome:
        """Revokes admission and drains child operations and retained contexts."""
        return self._domain._closeReservation(self)


class OperationClaim:
    """Issued execution protection with trusted adapter-only cleanup hooks.

    finish, retain and bindStop are internal adapter hooks. Pack/session facades must
    not expose caller-supplied evidence or provider payloads. Closing admission never
    revokes this exact claim's cleanup authority. Finish is terminal even if unknown;
    unknown blockers have no recovery API in ER-01.
    """

    def __init__(self, domain: ExecutionResourceDomain, owner: ResourceParticipant,
                 token: ResidencyToken, reservation: OwnerReservation | None,
                 retained: RetainedContextPin | None) -> None:
        """Creates an unissued claim; the domain installs it atomically."""
        self._domain, self._owner, self.token = domain, owner, token
        self._reservation, self._retained = reservation, retained
        self._revoked = False
        self._effect: ProviderEffect | None = None
        self._stop: Callable[[], None] | None = None
        self._stopBound = False
        self._stopDelivered = False
        self._stopRunning = False
        self._stopFailed = False
        self._unregister: Callable[[], None] | None = None

    @property
    def usable(self) -> bool:
        """Reports validity for new provider entry, not future cleanup authority."""
        with self._domain._lane:
            return (self in self._domain._active and not self._revoked and self._effect is None
                    and self._domain._phase not in (ResourceState.UNKNOWN, ResourceState.FAILED))

    def bindStop(self, callback: Callable[[], None]) -> Callable[[], None] | ResourceConflict:
        """Binds exactly once; late binding delivers any pending stop once.

        Unregister withdraws that callback but does not permit replacement. Stop errors
        are contained and cannot assert quiescence. Provider completion still must finish.
        """
        return self._domain._bindStop(self, callback)

    def requestStop(self) -> ReleaseOutcome | ResourceConflict:
        """Revokes use and requests cooperative stop without fabricating evidence."""
        with self._domain._lane:
            if type(self) is not OperationClaim or self not in self._domain._issuedClaims:
                return self._domain._conflict(ConflictReason.INVALID_HANDLE)
            if self._effect is not None:
                return ReleaseOutcome(self._domain._snapshot(), self._effect, True)
            self._revoked = True
        self._domain._drain()
        effect = (_UNCERTAIN if self._stopFailed else
                  ProviderEffect(EffectStatus.ATTEMPTED, Quiescence.UNKNOWN) if self._stopDelivered else None)
        return ReleaseOutcome(self._domain.snapshot, effect)

    def finish(self, effect: ProviderEffect) -> ReleaseOutcome | ResourceConflict:
        """Trusted adapter reports quiescence for this exact issued operation."""
        return self._domain._finish(self, effect)

    def retain(self, providerPayload: object, disposer: Callable[[object], ProviderEffect]
               ) -> RetainedContextPin | ResourceConflict:
        """Registers an adapter-owned context and concrete deferred disposer."""
        return self._domain._retain(self, providerPayload, disposer)


class RetainedContextPin:
    """Owned derived context protection between calls, separate from model weights."""

    def __init__(self, domain: ExecutionResourceDomain, claim: OperationClaim,
                 generation: int, payload: object, disposer: Callable[[object], ProviderEffect]) -> None:
        """Binds private provider state to exact owner, context and residency epochs."""
        self._domain, self._owner, self.token = domain, claim._owner, claim.token
        self.contextGeneration = generation
        self._reservation = claim._reservation
        self._payload, self._disposer = payload, disposer
        self._closed = False
        self._lost = False
        self._using: OperationClaim | None = claim
        self._disposing = False
        self._effect: ProviderEffect | None = None

    def close(self) -> ReleaseOutcome | ResourceConflict:
        """Requests revocation; actual disposal waits for confirmed operation drain."""
        with self._domain._lane:
            if type(self) is not RetainedContextPin or self not in self._domain._issuedPins:
                return self._domain._conflict(ConflictReason.INVALID_HANDLE)
            duplicate = self._closed
            self._closed = True
        self._domain._drain()
        return ReleaseOutcome(self._domain.snapshot, self._effect, duplicate)

    def invalidate(self) -> ReleaseOutcome | ResourceConflict:
        """Trusted adapter records loss of this exact context; never substitutes fresh."""
        with self._domain._lane:
            if type(self) is not RetainedContextPin or self not in self._domain._issuedPins:
                return self._domain._conflict(ConflictReason.INVALID_HANDLE)
            self._lost = True
        return self.close()


@dataclass(eq=False)
class _Transition:
    """Owns one in-flight callback sequence until publication or owned disposal."""

    owner: ResourceParticipant | None
    cancelled: bool = False


class ExecutionResourceDomain:
    """Serializes residency changes against issued operations, reservations and pins."""

    def __init__(self, domainId: str, adapter: ResidencyAdapter) -> None:
        """Creates an inert domain; trusted composition supplies its adapter."""
        if type(domainId) is not str or not domainId:
            raise ValueError("domainId must be a nonempty exact string.")
        self.domainId = domainId
        self._adapter = adapter
        self._lane = RLock()
        self._nonce = object()
        self._generation = 0
        self._contextGeneration = 0
        self._phase = ResourceState.EMPTY
        self._closed = False
        self._cleanupEnabled = True
        self._pendingUnregister: list[Callable[[], None]] = []
        self._token: ResidencyToken | None = None
        self._payload: object = None
        self._releaseAttempted = False
        self._ticket: _Transition | None = None
        self._profiles: dict[bytes, ProfileReference] = {}
        self._owners: WeakSet[ResourceParticipant] = WeakSet()
        self._issuedClaims: WeakSet[OperationClaim] = WeakSet()
        self._issuedPins: WeakSet[RetainedContextPin] = WeakSet()
        self._issuedReservations: WeakSet[OwnerReservation] = WeakSet()
        self._active: set[OperationClaim] = set()
        self._pins: set[RetainedContextPin] = set()
        self._reservation: OwnerReservation | None = None

    @property
    def snapshot(self) -> ResourceSnapshot:
        """Returns bounded core evidence without rendering any adapter object."""
        with self._lane:
            return self._snapshot()

    def _snapshot(self) -> ResourceSnapshot:
        """Builds a snapshot while the domain lane is held."""
        state = self._phase
        if state not in (ResourceState.UNKNOWN, ResourceState.FAILED):
            if self._ticket is not None:
                state = ResourceState.DRAINING if self._closed else ResourceState.TRANSITIONING
            elif self._closed:
                state = ResourceState.DRAINING if self._token or self._active or self._pins else ResourceState.CLOSED
            elif any(c._revoked for c in self._active) or any(p._closed for p in self._pins):
                state = ResourceState.DRAINING
            elif self._reservation is not None and self._reservation._closed:
                state = ResourceState.DRAINING
        return ResourceSnapshot(state, self._token, len(self._active), len(self._pins), self._reservation is not None)

    def _conflict(self, reason: ConflictReason) -> ResourceConflict:
        """Builds a safe rejection under the domain lane."""
        return ResourceConflict(reason, self._snapshot())

    def registerProfile(self, descriptor: ResidencyDescriptor) -> ProfileReference | ResourceConflict:
        """Trusted host registration is idempotent only for the same descriptor."""
        if type(descriptor) is not ResidencyDescriptor:
            raise TypeError("Expected a ResidencyDescriptor.")
        with self._lane:
            if self._closed:
                return self._conflict(ConflictReason.CLOSED)
            existing = self._profiles.get(descriptor.identity)
            if existing is not None:
                return existing if existing._descriptor is descriptor else self._conflict(ConflictReason.INVALID_HANDLE)
            profile = ProfileReference(descriptor)
            self._profiles[descriptor.identity] = profile
            return profile

    def openParticipant(self, *, allowedProfiles: Iterable[ProfileReference], allowIdleTransition: bool = False
                        ) -> ResourceParticipant | ResourceConflict:
        """Trusted composition issues grants; no owner-label assertion conveys authority."""
        profiles = frozenset(allowedProfiles)
        if type(allowIdleTransition) is not bool:
            raise TypeError("Host transition grant must be bool.")
        with self._lane:
            if self._closed:
                return self._conflict(ConflictReason.CLOSED)
            if any(type(p) is not ProfileReference or self._profiles.get(p._descriptor.identity) is not p for p in profiles):
                return self._conflict(ConflictReason.INVALID_HANDLE)
            owner = ResourceParticipant(self, profiles, allowIdleTransition)
            self._owners.add(owner)
            return owner

    def _validate(self, owner: ResourceParticipant, profile: ProfileReference | None) -> ResourceConflict | None:
        """Checks exact owner/grants for new use; cleanup deliberately bypasses this."""
        if type(owner) is not ResourceParticipant or owner not in self._owners:
            return self._conflict(ConflictReason.INVALID_HANDLE)
        if self._closed or owner._closed:
            return self._conflict(ConflictReason.CLOSED)
        if profile is not None and (type(profile) is not ProfileReference or profile not in owner._profiles):
            return self._conflict(ConflictReason.UNAUTHORIZED)
        return None

    def _unavailable(self) -> ResourceConflict | None:
        """Reports fail-closed transient and terminal admission gates."""
        reasons = {ResourceState.UNKNOWN: ConflictReason.UNKNOWN_QUIESCENCE,
                   ResourceState.FAILED: ConflictReason.FAILED,
                   ResourceState.DRAINING: ConflictReason.DRAINING,
                   ResourceState.TRANSITIONING: ConflictReason.TRANSITIONING,
                   ResourceState.CLOSED: ConflictReason.CLOSED}
        reason = reasons.get(self._snapshot().state)
        return self._conflict(reason) if reason else None

    def _compatible(self, profile: ProfileReference) -> bool:
        """Compares prevalidated bytes only; provider equality is never involved."""
        return self._token is not None and self._token.identity == profile._descriptor.identity

    def _acquire(self, owner: ResourceParticipant, profile: ProfileReference,
                 reservation: OwnerReservation | None, retained: RetainedContextPin | None,
                 cancellation: CancellationSignal | None) -> OperationClaim | ResourceConflict:
        """Installs protection before cancellation registration, then checks revocation."""
        if cancellation is not None and not isinstance(cancellation, CancellationSignal):
            raise TypeError("Expected CancellationSignal.")
        cancelled = cancellation is not None and cancellation.requested
        with self._lane:
            error = self._validate(owner, profile) or self._unavailable()
            if error:
                return error
            if cancelled:
                return self._conflict(ConflictReason.CANCELLED)
            if not self._compatible(profile):
                return self._conflict(ConflictReason.NOT_RESIDENT)
            if reservation is not None and (reservation is not self._reservation or reservation._owner is not owner or reservation._closed):
                return self._conflict(ConflictReason.INVALID_HANDLE)
            if self._reservation is not None and reservation is not self._reservation:
                return self._conflict(ConflictReason.BUSY)
            if reservation is not None and self._active:
                return self._conflict(ConflictReason.BUSY)
            if retained is not None:
                if type(retained) is not RetainedContextPin or retained not in self._issuedPins or retained._owner is not owner:
                    return self._conflict(ConflictReason.INVALID_HANDLE)
                if retained._lost or retained._closed or retained.token is not self._token:
                    return self._conflict(ConflictReason.CONTEXT_LOST)
                if retained._using is not None:
                    return self._conflict(ConflictReason.BUSY)
            claim = OperationClaim(self, owner, self._token, reservation, retained)
            self._issuedClaims.add(claim)
            self._active.add(claim)
            if retained is not None:
                retained._using = claim
                retained._reservation = reservation
        if cancellation is not None:
            try:
                unregister = cancellation.registerCallback(claim.requestStop)
            except Exception:  # noqa: BLE001 - unpublished admission must not strand protection.
                claim.finish(_CONFIRMED)
                with self._lane:
                    return self._conflict(ConflictReason.FAILED)
            with self._lane:
                claim._unregister = unregister
        with self._lane:
            revoked = claim._revoked or owner._closed or self._closed
        if revoked:
            # No adapter has received the claim yet, so no provider work began.
            claim.finish(_CONFIRMED)
            with self._lane:
                return self._conflict(ConflictReason.CANCELLED)
        return claim

    def _reserve(self, owner: ResourceParticipant, profile: ProfileReference) -> OwnerReservation | ResourceConflict:
        """Atomically excludes unrelated admission without waiting or preemption."""
        with self._lane:
            error = self._validate(owner, profile) or self._unavailable()
            if error:
                return error
            if not self._compatible(profile):
                return self._conflict(ConflictReason.NOT_RESIDENT)
            if self._active or self._reservation or any(p._owner is not owner for p in self._pins):
                return self._conflict(ConflictReason.BUSY)
            reservation = OwnerReservation(self, owner, self._token)
            self._issuedReservations.add(reservation)
            self._reservation = reservation
            return reservation

    def _bindStop(self, claim: OperationClaim, callback: Callable[[], None]) -> Callable[[], None] | ResourceConflict:
        """Installs a single callback without losing requests that preceded binding."""
        if not callable(callback):
            raise TypeError("Stop callback must be callable.")
        with self._lane:
            if type(claim) is not OperationClaim or claim not in self._issuedClaims or claim._stopBound or claim._effect is not None:
                return self._conflict(ConflictReason.INVALID_HANDLE)
            claim._stopBound = True
            claim._stop = callback
        self._drain()

        def unregister() -> None:
            """Withdraws this binding without permitting callback replacement."""
            with self._lane:
                claim._stop = None

        return unregister

    def _finish(self, claim: OperationClaim, effect: ProviderEffect) -> ReleaseOutcome | ResourceConflict:
        """Accepts exact issued cleanup even after owner/domain/reservation revocation."""
        if type(effect) is not ProviderEffect:
            raise TypeError("Only typed trusted adapter evidence is accepted.")
        with self._lane:
            if type(claim) is not OperationClaim or claim not in self._issuedClaims:
                return self._conflict(ConflictReason.INVALID_HANDLE)
            if claim._effect is not None:
                if claim._effect != effect:
                    return self._conflict(ConflictReason.INVALID_HANDLE)
                return ReleaseOutcome(self._snapshot(), claim._effect, True)
            claim._effect = effect
            claim._revoked = True
            unregister, claim._unregister = claim._unregister, None
            if unregister is not None:
                self._pendingUnregister.append(unregister)
            if effect.quiescence is Quiescence.UNKNOWN:
                self._phase = ResourceState.UNKNOWN
            elif not claim._stopRunning:
                self._retireClaim(claim)
        self._drain()
        return ReleaseOutcome(self.snapshot, effect)

    def _retain(self, claim: OperationClaim, payload: object, disposer: Callable[[object], ProviderEffect]
                ) -> RetainedContextPin | ResourceConflict:
        """Transfers generic context cleanup to an exact registered disposer ticket."""
        if not callable(disposer):
            raise TypeError("Context disposer must be callable.")
        with self._lane:
            if type(claim) is not OperationClaim or claim not in self._active or claim._revoked or claim._effect is not None or self._closed:
                return self._conflict(ConflictReason.INVALID_HANDLE)
            self._contextGeneration += 1
            pin = RetainedContextPin(self, claim, self._contextGeneration, payload, disposer)
            self._issuedPins.add(pin)
            self._pins.add(pin)
            return pin

    def _retireClaim(self, claim: OperationClaim) -> None:
        """Removes confirmed work only after any dispatched stop callback returns."""
        self._active.discard(claim)
        for pin in self._pins:
            if pin._using is claim:
                pin._using = None

    def _closeParticipant(self, owner: ResourceParticipant) -> ReleaseOutcome | ResourceConflict:
        """Revokes owned use while leaving other owners and cleanup authority intact."""
        with self._lane:
            if type(owner) is not ResourceParticipant or owner not in self._owners:
                return self._conflict(ConflictReason.INVALID_HANDLE)
            duplicate = owner._closed
            owner._closed = True
            self._revoke(owner=owner)
        self._drain()
        return ReleaseOutcome(self.snapshot, duplicate=duplicate)

    def _closeReservation(self, reservation: OwnerReservation) -> ReleaseOutcome | ResourceConflict:
        """Drains a reservation's children before withdrawing its exclusion."""
        with self._lane:
            if type(reservation) is not OwnerReservation or reservation not in self._issuedReservations:
                return self._conflict(ConflictReason.INVALID_HANDLE)
            if reservation is not self._reservation:
                if reservation._domain is self and reservation._closed:
                    return ReleaseOutcome(self._snapshot(), duplicate=True)
                return self._conflict(ConflictReason.INVALID_HANDLE)
            duplicate = reservation._closed
            reservation._closed = True
            self._revoke(reservation=reservation)
        self._drain()
        return ReleaseOutcome(self.snapshot, duplicate=duplicate)

    def _revoke(self, *, owner: ResourceParticipant | None = None, reservation: OwnerReservation | None = None) -> None:
        """Marks matching work under lock, making no calls to provider or owner code."""
        for claim in self._active:
            if (owner is None or claim._owner is owner) and (reservation is None or claim._reservation is reservation):
                claim._revoked = True
        for pin in self._pins:
            if (owner is None or pin._owner is owner) and (reservation is None or pin._reservation is reservation):
                pin._closed = True
        if self._reservation is not None and (owner is None or self._reservation._owner is owner):
            if reservation is None or reservation is self._reservation:
                self._reservation._closed = True

    def _drain(self) -> None:
        """Delivers deferred stop/disposal once, outside every core lock.

        In-progress work stays installed during callback reentry. Unknown operation or
        context evidence retains the appropriate blocker permanently in this slice.
        """
        with self._lane:
            if not self._cleanupEnabled:
                return
            unregisters, self._pendingUnregister = self._pendingUnregister, []
            stops = []
            for claim in self._active:
                if claim._revoked and claim._effect is None and claim._stop is not None and not claim._stopDelivered:
                    claim._stopDelivered = True
                    claim._stopRunning = True
                    stops.append((claim, claim._stop))
        for unregister in unregisters:
            unregister()
        for claim, callback in stops:
            try:
                callback()
            except Exception:  # noqa: BLE001 - stop failure must not escape cancellation delivery.
                with self._lane:
                    claim._stopFailed = True
            finally:
                with self._lane:
                    claim._stopRunning = False
                    if claim._effect is not None and claim._effect.quiescence is Quiescence.CONFIRMED:
                        self._retireClaim(claim)
        with self._lane:
            pins = tuple(p for p in self._pins if p._closed and p._using is None and not p._disposing and p._effect is None)
            for pin in pins:
                pin._disposing = True
        for pin in pins:
            effect = self._callEffect(pin._disposer, pin._payload)
            with self._lane:
                pin._effect = effect
                if effect == _CONFIRMED:
                    self._pins.remove(pin)
                    pin._payload = None
                    pin._disposer = _disposedContext
                else:
                    if self._phase is not ResourceState.UNKNOWN:
                        self._phase = ResourceState.UNKNOWN if effect.quiescence is Quiescence.UNKNOWN else ResourceState.FAILED
        with self._lane:
            reservation = self._reservation
            if reservation is not None and reservation._closed:
                if not any(c._reservation is reservation for c in self._active) and not any(p._reservation is reservation for p in self._pins):
                    self._reservation = None

    @staticmethod
    def _callEffect(callback: Callable[..., ProviderEffect], *args: object) -> ProviderEffect:
        """Contains adapter exceptions and malformed evidence without private text."""
        try:
            effect = callback(*args)
            return effect if type(effect) is ProviderEffect else _UNCERTAIN
        except Exception:  # noqa: BLE001 - arbitrary trusted adapter failures quarantine safely.
            return _UNCERTAIN

    def _releaseOwned(self, token: ResidencyToken, payload: object) -> ProviderEffect:
        """Contains release hook resolution as well as callback execution failures."""
        try:
            return self._callEffect(self._adapter.release, token, payload)
        except Exception:  # noqa: BLE001 - broken adapter binding cannot strand a transition ticket.
            return _UNCERTAIN

    def _transition(self, owner: ResourceParticipant, profile: ProfileReference | None,
                    cancellation: CancellationSignal | None) -> TransitionOutcome | ResourceConflict:
        """Owns release/load/publication and any unpublished-result disposal as one ticket."""
        if cancellation is not None and not isinstance(cancellation, CancellationSignal):
            raise TypeError("Expected CancellationSignal.")
        cancelled = cancellation is not None and cancellation.requested
        with self._lane:
            error = self._validate(owner, profile)
            if error:
                return error
            if not owner._transition:
                return self._conflict(ConflictReason.UNAUTHORIZED)
            if self._phase is ResourceState.FAILED and self._token is None and self._ticket is None:
                pass  # Explicit retry is allowed only with confirmed-empty evidence.
            else:
                error = self._unavailable()
                if error:
                    return error
            if self._active or self._pins or self._reservation:
                return self._conflict(ConflictReason.BUSY)
            if cancelled:
                return self._conflict(ConflictReason.CANCELLED)
            if profile is not None and self._compatible(profile):
                return TransitionOutcome(self._snapshot(), _CONFIRMED)
            ticket = _Transition(owner)
            self._ticket = ticket
        try:
            unregister = cancellation.registerCallback(lambda: self._cancelTransition(ticket)) if cancellation is not None else None
        except Exception:  # noqa: BLE001 - no provider action started; relinquish this exact ticket.
            with self._lane:
                if self._ticket is ticket:
                    self._ticket = None
                return self._conflict(ConflictReason.FAILED)
        try:
            return self._runTransition(ticket, profile)
        finally:
            if unregister is not None:
                unregister()

    def _cancelTransition(self, ticket: _Transition) -> None:
        """Revokes a pending attempt without pretending its callback has completed."""
        with self._lane:
            if self._ticket is ticket:
                ticket.cancelled = True

    def _cancelled(self, ticket: _Transition) -> bool:
        """Checks caller/domain revocation under the lane, preserving cleanup rights."""
        return ticket.cancelled or self._closed or (ticket.owner is not None and ticket.owner._closed)

    def _runTransition(self, ticket: _Transition, profile: ProfileReference | None) -> TransitionOutcome:
        """Runs provider calls outside locks, publishing only on the current ticket."""
        with self._lane:
            oldToken, oldPayload = self._token, self._payload
            cancelled = self._cancelled(ticket)
        effect: ProviderEffect | None = None
        if oldToken is not None and not cancelled:
            with self._lane:
                self._releaseAttempted = True
            effect = self._releaseOwned(oldToken, oldPayload)
            with self._lane:
                if effect != _CONFIRMED:
                    self._phase = ResourceState.UNKNOWN if effect.quiescence is Quiescence.UNKNOWN else ResourceState.FAILED
                    self._ticket = None
                    return TransitionOutcome(self._snapshot(), effect, self._cancelled(ticket))
                self._token, self._payload = None, None
                self._phase = ResourceState.EMPTY
        with self._lane:
            if profile is None or self._cancelled(ticket):
                self._ticket = None
                return TransitionOutcome(self._snapshot(), effect, self._cancelled(ticket))
            self._generation += 1
            token = ResidencyToken(self._nonce, self._generation, profile._descriptor.identity)
        try:
            result = self._adapter.materialize(profile._descriptor, token)
            if type(result) is not Materialization:
                result = Materialization(_UNCERTAIN, False)
        except Exception:  # noqa: BLE001 - readiness exceptions cannot establish absence of resources.
            result = Materialization(_UNCERTAIN, False)
        with self._lane:
            cancelled = self._cancelled(ticket)
            if self._ticket is ticket and not cancelled and result.present and result.effect == _CONFIRMED:
                self._token, self._payload = token, result.providerPayload
                self._releaseAttempted = False
                self._phase = ResourceState.READY
                self._ticket = None
                return TransitionOutcome(self._snapshot(), result.effect, materializationEffect=result.effect)
        effect = result.effect
        if result.present:
            # The attempt owns its unpublished object even after admission closes.
            effect = self._releaseOwned(token, result.providerPayload)
        with self._lane:
            if self._ticket is ticket:
                self._ticket = None
                self._token, self._payload = None, None
                if effect == _CONFIRMED and (result.present or cancelled):
                    self._phase = ResourceState.EMPTY
                elif effect.quiescence is Quiescence.CONFIRMED:
                    self._phase = ResourceState.FAILED
                else:
                    self._phase = ResourceState.UNKNOWN
                if result.present and effect != _CONFIRMED:
                    self._token, self._payload = token, result.providerPayload
                    self._releaseAttempted = True
            return TransitionOutcome(self._snapshot(), effect, self._cancelled(ticket), result.effect)

    def close(self) -> ReleaseOutcome:
        """Closes admission and defers callbacks until outside-lock finishClose.

        Late operation completion during legacy teardown can record evidence but cannot
        dispatch stop, unregister or context-disposal callbacks through this first stage.
        """
        with self._lane:
            duplicate = self._closed
            self._closed = True
            self._cleanupEnabled = False
            for owner in self._owners:
                owner._closed = True
            self._revoke()
            return ReleaseOutcome(self._snapshot(), duplicate=duplicate)

    def finishClose(self) -> ReleaseOutcome:
        """Attempts deferred shutdown outside host locks; never repeats uncertain release."""
        self.close()
        with self._lane:
            self._cleanupEnabled = True
        self._drain()
        with self._lane:
            if self._ticket or self._active or self._pins or self._reservation or self._token is None or self._releaseAttempted:
                return ReleaseOutcome(self._snapshot())
            # Unknown/failed operation state cannot justify disposal or reuse.
            if self._phase in (ResourceState.UNKNOWN, ResourceState.FAILED):
                return ReleaseOutcome(self._snapshot())
            self._releaseAttempted = True
            ticket = _Transition(None)
            self._ticket = ticket
            token, payload = self._token, self._payload
        effect = self._releaseOwned(token, payload)
        with self._lane:
            if self._ticket is ticket:
                self._ticket = None
                if effect == _CONFIRMED:
                    self._token, self._payload = None, None
                    self._phase = ResourceState.EMPTY
                else:
                    self._phase = ResourceState.UNKNOWN if effect.quiescence is Quiescence.UNKNOWN else ResourceState.FAILED
            return ReleaseOutcome(self._snapshot(), effect)


def _disposedContext(payload: object) -> ProviderEffect:
    """Drops disposer closure references after successful exactly-once cleanup."""
    return _CONFIRMED


class ExecutionResourceService:
    """Host lifetime table, retaining unavailable domains instead of recreating them."""

    def __init__(self) -> None:
        """Constructs an inert host facility with no provider or hardware activation."""
        self._lane = RLock()
        self._domains: dict[str, tuple[bytes, ResidencyAdapter, ExecutionResourceDomain]] = {}
        self._closed = False

    def registerDomain(self, domainId: str, *, compatibilityKey: bytes,
                       adapter: ResidencyAdapter) -> ExecutionResourceDomain:
        """Trusted host registers one exact adapter/envelope; replacement is forbidden."""
        if type(domainId) is not str or not domainId or type(compatibilityKey) is not bytes or not compatibilityKey:
            raise ValueError("Domain ID and envelope key must be nonempty exact values.")
        with self._lane:
            if self._closed:
                raise RuntimeError("ExecutionResourceService is closed.")
            existing = self._domains.get(domainId)
            if existing is not None:
                key, issuedAdapter, domain = existing
                if key != compatibilityKey or issuedAdapter is not adapter:
                    raise RuntimeError("Execution domain registration conflicts.")
                return domain
            domain = ExecutionResourceDomain(domainId, adapter)
            self._domains[domainId] = (compatibilityKey, adapter, domain)
            return domain

    @property
    def snapshots(self) -> tuple[ResourceSnapshot, ...]:
        """Returns safe retained-domain evidence, including shutdown tombstones."""
        with self._lane:
            domains = tuple(record[2] for record in self._domains.values())
        return tuple(domain.snapshot for domain in domains)

    def close(self) -> None:
        """Revokes all admission without invoking adapter/user callbacks."""
        with self._lane:
            self._closed = True
            domains = tuple(record[2] for record in self._domains.values())
        for domain in domains:
            domain.close()

    def finishClose(self) -> None:
        """Attempts each cleanup outside locks and raises safe aggregate disposition."""
        self.close()
        with self._lane:
            domains = tuple(record[2] for record in self._domains.values())
        unresolved = tuple(result.snapshot for domain in domains
                           if (result := domain.finishClose()).snapshot.state is not ResourceState.CLOSED)
        if unresolved:
            raise ResourceCleanupError(unresolved)
