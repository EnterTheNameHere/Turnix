# file: backend/observation.py ; version: 2
"""Authorized application observation composition over the accepted PM-01/02 seams."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from uuid import uuid4
from weakref import WeakSet

from backend.llm.streamObservation import (
    StreamObservation, StreamObservationConfiguration, StreamObservationSnapshot,
    StreamObservationWindow,
)
from backend.telemetry import (
    TelemetryConfiguration, TelemetryMode, TelemetryService, TelemetrySnapshot,
    TelemetrySubscription, createDefaultTelemetryService,
)


class ObservationLifetime(StrEnum):
    """Selects run cleanup or explicit independent ApplicationRun ownership."""

    RUN = "run"
    APPLICATION = "application"


@dataclass(frozen=True, slots=True)
class ObservationRequest:
    """Provider-neutral needs; all retained histories have finite configured bounds.

    Hardware groups are whole-host observations, never per-application attribution.
    Stream counts measure delivered Unicode characters, never model tokens.
    """

    memory: bool = True
    cpu: bool = True
    gpu: bool = True
    stream: bool = True
    sampleIntervalSeconds: float = 1.0
    historySamples: int = 300
    windowSeconds: float = 1.0
    historyWindows: int = 120

    def __post_init__(self) -> None:
        """Validates needs and delegates cadence/retention validation to PM-01/02."""
        for name in ("memory", "cpu", "gpu", "stream"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be bool.")
        self.telemetryConfiguration()
        self.streamConfiguration()

    def telemetryConfiguration(self) -> TelemetryConfiguration:
        """Translates signal needs internally without selecting hardware providers."""
        return TelemetryConfiguration(
            memoryMode=TelemetryMode.HISTORY if self.memory else TelemetryMode.DISABLED,
            cpuMode=TelemetryMode.HISTORY if self.cpu else TelemetryMode.DISABLED,
            gpuMode=TelemetryMode.HISTORY if self.gpu else TelemetryMode.DISABLED,
            sampleIntervalSeconds=self.sampleIntervalSeconds, historySamples=self.historySamples,
        )

    def streamConfiguration(self) -> StreamObservationConfiguration:
        """Builds the accepted bounded stream-window configuration."""
        return StreamObservationConfiguration(self.windowSeconds, self.historyWindows)


@dataclass(frozen=True, slots=True)
class CorrelatedWindow:
    """Interval overlap only; reads are sequential, with no utilization interpolation.

    uncoveredIntervals are portions with no retained collection activity, not proof
    of lost polls. Normal cadence, eviction and unavailability can all leave gaps.
    Unknown-timing snapshots remain in ObservationSnapshot.hardware, unaligned.
    """

    stream: StreamObservationWindow
    hardware: tuple[TelemetrySnapshot, ...]
    uncoveredIntervals: tuple[tuple[int, int], ...]


def correlate(
    windows: tuple[StreamObservationWindow, ...], hardware: tuple[TelemetrySnapshot, ...],
) -> tuple[CorrelatedWindow, ...]:
    """Joins bounded evidence in one host monotonic domain, ignoring wall timestamps.

    Positive intervals use half-open overlap. Instantaneous hardware reads belong
    to the containing window; zero-duration stream windows match containing reads.
    Overlap describes possible temporal association, never simultaneous sensors.
    """
    result = []
    for window in windows:
        matches = []
        spans = []
        for sample in hardware:
            start, end = sample.collectionStartNs, sample.collectionEndNs
            if start is None or end is None or start < 0 or end < start:
                continue
            if start == end:
                overlap = window.startNs <= start < window.endNs or start == window.startNs == window.endNs
            elif window.startNs == window.endNs:
                overlap = start <= window.startNs < end
            else:
                overlap = start < window.endNs and end > window.startNs
            if overlap:
                matches.append(sample)
                spans.append((max(start, window.startNs), min(end, window.endNs)))
        cursor = window.startNs
        gaps = []
        for start, end in sorted(spans):
            if start > cursor:
                gaps.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < window.endNs:
            gaps.append((cursor, window.endNs))
        result.append(CorrelatedWindow(window, tuple(matches), tuple(gaps)))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ObservationSnapshot:
    """Disposable bounded evidence; wall time exists only on raw hardware samples.

    hardwareHistoryMayBeTruncated is conservative: PM-01 exposes no eviction
    counter. A full buffer may have lost earlier samples; an exact count is unknown.
    Stream truncation is explicit in stream.droppedWindowCount. Native token data
    remains separately owned by the LLM result's providerMetadata.
    """

    hardware: tuple[TelemetrySnapshot, ...]
    stream: StreamObservationSnapshot | None
    windows: tuple[CorrelatedWindow, ...]
    hardwareHistoryMayBeTruncated: bool
    hardwareUnavailableReason: str | None
    streamUnavailableReason: str | None
    released: bool
    collectionStopped: bool


class ObservationGrantRevoked(RuntimeError):
    """Reports loss of selected read authority without returning stale evidence."""


@dataclass(frozen=True, slots=True)
class ObservationPublicationSnapshot:
    """Selected immutable evidence with opaque source/clock identities, never invocation state."""

    sessionId: str
    clockEpochId: str
    lifetime: ObservationLifetime
    request: ObservationRequest
    observation: ObservationSnapshot


class ObservationReadGrant:
    """Revocable snapshot authority only; construction belongs to the observation facade.

    Grants add no source lifetime/collection claim. Parent withdrawal invalidates
    derived readers; revoking a derived reader affects neither its parent nor peers.
    Python Pack code is trusted in-process code, not a Python introspection sandbox.
    No source/context/registry access is part of this object's public interface.
    """

    __slots__ = ("__source", "__parent", "__lock", "__revoked", "__weakref__")

    def __init__(self, source: ObservationSession, parent: ObservationReadGrant | None = None) -> None:
        """Creates a mediated read scope under the owning service's lifecycle lock."""
        self.__source = source
        self.__parent = parent
        self.__lock = source._owner._service._lock
        self.__revoked = False
        source._readGrants.add(self)

    def _validSource(self) -> ObservationSession:
        """Checks the entire delegation chain while the shared lifecycle lock is held."""
        cursor = self
        while cursor is not None:
            source = cursor.__source
            if cursor.__revoked or source is None or source._released or source._owner._released:
                raise ObservationGrantRevoked("Observation read authority is revoked.")
            cursor = cursor.__parent
        return self.__source

    @property
    def revoked(self) -> bool:
        """Reports effective revocation without reading hardware or stream clocks."""
        with self.__lock:
            try:
                self._validSource()
            except ObservationGrantRevoked:
                return True
            return False

    def snapshot(self) -> ObservationPublicationSnapshot:
        """Reads once under source authority; previously returned evidence stays caller-owned."""
        with self.__lock:
            source = self._validSource()
            return ObservationPublicationSnapshot(
                source._sessionId, source._owner._service._clockEpochId,
                source.lifetime, source.request, source.snapshot(),
            )

    def revoke(self) -> None:
        """Relinquishes only this read scope, without releasing or cancelling its source."""
        with self.__lock:
            if self.__source is not None:
                self.__source._readGrants.discard(self)
            self.__revoked = True
            self.__source = None
            self.__parent = None

    def _bind(self, owner: ObservationOwner) -> ObservationReadGrant:
        """Derives an independently revocable reader only for the same ApplicationRun."""
        if owner is None or owner._service._lock is not self.__lock:
            raise PermissionError("Observation grant belongs to another application.")
        with self.__lock:
            source = self._validSource()
            if source._owner is not owner or source not in owner._sessions:
                raise PermissionError("Observation grant belongs to another application.")
            return ObservationReadGrant(source, self)


class ObservationService:
    """Host composition boundary; owns scopes, borrows the shared telemetry service.

    Closing revokes only scopes created here. Legacy and independently acquired
    telemetry subscriptions survive. Construction starts no provider or polling.
    Injected clocks must share the telemetry service's host monotonic domain.
    A random composition epoch prevents publication readers from joining evidence
    across reconstructed services or differently injected monotonic clock domains.
    """

    def __init__(self, telemetry: TelemetryService | None = None, *, monotonicNs: Callable[[], int] = time.monotonic_ns) -> None:
        """Selects host infrastructure without exposing providers to applications."""
        self._telemetry = telemetry if telemetry is not None else createDefaultTelemetryService()
        self._clock = monotonicNs
        self._clockEpochId = uuid4().hex
        self._lock = RLock()
        self._owners: set[ObservationOwner] = set()
        self._closed = False

    def owner(self) -> ObservationOwner:
        """Creates independent ApplicationRun authority until explicit revocation."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Observation host is closed.")
            owner = ObservationOwner(self)
            self._owners.add(owner)
            return owner

    def close(self) -> None:
        """Revokes every owned application scope without stopping unrelated telemetry."""
        with self._lock:
            self._closed = True
            for owner in tuple(self._owners):
                owner.release()


class ObservationOwner:
    """Independent ApplicationRun lifetime authority, with no invocation references."""

    def __init__(self, service: ObservationService) -> None:
        """Creates an inactive runtime-owned scope; applications receive sessions only."""
        self._service = service
        self._sessions: set[ObservationSession] = set()
        self._released = False

    def request(self, request: ObservationRequest, lifetime: ObservationLifetime) -> ObservationSession:
        """Acquires a session after the context facade has checked invocation authority."""
        if not isinstance(request, ObservationRequest) or not isinstance(lifetime, ObservationLifetime):
            raise TypeError("Expected ObservationRequest and ObservationLifetime.")
        with self._service._lock:
            if self._released:
                raise RuntimeError("Observation application owner is released.")
            session = ObservationSession(self, request, lifetime)
            self._sessions.add(session)
            return session

    def release(self) -> None:
        """Revokes this application's sessions, leaving other applications independent."""
        with self._service._lock:
            self._released = True
            for session in tuple(self._sessions):
                session.release()
            self._service._owners.discard(self)


class ObservationSession:
    """One optional stream plus hardware claim under explicit lifetime ownership.

    A session retains neither CodeEntryContext nor an authority callback. It cannot
    execute inference. RUN collection stops on every facade execution exit; unused
    RUN sessions are disposed on invocation invalidation. APPLICATION collection
    continues until release or runtime/host teardown, independently of invocation.
    """

    def __init__(self, owner: ObservationOwner, request: ObservationRequest, lifetime: ObservationLifetime) -> None:
        """Acquires optional facilities, recording ordinary failures as unavailability."""
        self._owner = owner
        self._sessionId = uuid4().hex
        self._readGrants: WeakSet[ObservationReadGrant] = WeakSet()
        self.request = request
        self.lifetime = lifetime
        self._subscription: TelemetrySubscription | None = None
        self._stream: StreamObservation | None = None
        self._hardware: tuple[TelemetrySnapshot, ...] = ()
        self._hardwareError: str | None = None
        self._streamError: str | None = None
        self._released = False
        self._stopped = False
        self._used = False
        if request.memory or request.cpu or request.gpu:
            try:
                self._subscription = owner._service._telemetry.subscribe(request.telemetryConfiguration())
            except Exception as err:
                self._hardwareError = type(err).__name__
        if request.stream:
            try:
                self._stream = StreamObservation(request.streamConfiguration(), monotonicNs=owner._service._clock)
            except Exception as err:
                self._streamError = type(err).__name__

    def forExecution(self, owner: ObservationOwner | None) -> StreamObservation | None:
        """Validates same-application single-use attachment without extending authority."""
        with self._owner._service._lock:
            if owner is not self._owner or self._owner._released or self._released or self._used:
                raise RuntimeError("Observation session is foreign, revoked or already used.")
            self._used = True
            return self._stream

    def finishExecution(self) -> None:
        """Stops RUN hardware on success/failure/cancellation without changing outcomes."""
        with self._owner._service._lock:
            if self.lifetime is ObservationLifetime.RUN:
                self._stopCollection()

    def _stopCollection(self) -> None:
        """Freezes bounded hardware evidence before releasing this collection claim."""
        if self._subscription is not None:
            try:
                self._hardware = self._subscription.history
            except Exception as err:
                self._hardwareError = type(err).__name__
            try:
                self._subscription.release()
            except Exception as err:
                self._hardwareError = type(err).__name__
            self._subscription = None
        self._stopped = True

    def snapshot(self) -> ObservationSnapshot:
        """Returns immutable evidence without physical reads; live stream time advances."""
        with self._owner._service._lock:
            hardware = self._hardware
            if self._subscription is not None:
                try:
                    hardware = self._subscription.history
                except Exception as err:
                    self._hardwareError = type(err).__name__
            stream = None
            if self._stream is not None:
                try:
                    stream = self._stream.snapshot()
                except Exception as err:
                    self._streamError = type(err).__name__
            return ObservationSnapshot(
                hardware, stream, correlate(() if stream is None else stream.windows, hardware),
                len(hardware) >= self.request.historySamples,
                self._hardwareError, self._streamError, self._released, self._stopped,
            )

    def release(self) -> None:
        """Idempotently revokes observation only, clears evidence and never cancels work."""
        with self._owner._service._lock:
            if self._released:
                return
            for grant in tuple(self._readGrants):
                grant.revoke()
            self._stopCollection()
            if self._stream is not None:
                try:
                    self._stream.release()
                except Exception as err:
                    self._streamError = type(err).__name__
                self._stream = None
            self._hardware = ()
            self._released = True
            self._owner._sessions.discard(self)


class ObservationFacade:
    """Invocation-authorized request gateway; only this facade retains validation."""

    def __init__(self, owner: ObservationOwner | None, requireValid: Callable[[], None]) -> None:
        """Binds request authority and tracks RUN disposal independently of sessions."""
        self._owner = owner
        self._requireValid = requireValid
        self._sessions: list[ObservationSession] = []
        self._lock = RLock()
        self._closed = False

    def request(self, request: ObservationRequest | None = None, *, lifetime: ObservationLifetime = ObservationLifetime.RUN) -> ObservationSession:
        """Checks invocation and independent application authority before acquisition."""
        with self._lock:
            self._requireValid()
            if self._closed:
                raise RuntimeError("Observation invocation is closed.")
            if self._owner is None:
                raise RuntimeError("No application observation owner is bound.")
            session = self._owner.request(ObservationRequest() if request is None else request, lifetime)
            if lifetime is ObservationLifetime.RUN:
                self._sessions.append(session)
            return session

    def close(self) -> None:
        """Disposes RUN sessions when invocation authority ends; application scopes persist."""
        with self._lock:
            self._closed = True
            for session in self._sessions:
                session.release()
            self._sessions.clear()

    def export(self, session: ObservationSession) -> ObservationReadGrant:
        """Exports only an explicitly selected same-ApplicationRun live session.

        This checks invocation authority but never extends RUN lifetime. The grant
        retains no context/callback and delegates only read/revocation of itself.
        """
        with self._lock:
            self._requireValid()
            if self._closed or self._owner is None:
                raise RuntimeError("Observation invocation is closed or unbound.")
            with self._owner._service._lock:
                if type(session) is not ObservationSession or session not in self._owner._sessions or self._owner._released:
                    raise PermissionError("Observation session is foreign or revoked.")
                return ObservationReadGrant(session)

    def bindReadGrant(self, grant: ObservationReadGrant) -> ObservationReadGrant:
        """Validates same-app authority and acquires a separately revocable publication reader."""
        with self._lock:
            self._requireValid()
            if self._closed or self._owner is None:
                raise RuntimeError("Observation invocation is closed or unbound.")
            if type(grant) is not ObservationReadGrant:
                raise TypeError("An ObservationReadGrant is required.")
            return grant._bind(self._owner)
