# file: backend/telemetry/service.py ; version: 7
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, replace
from threading import Condition, RLock, Thread, current_thread
from typing import TYPE_CHECKING

from backend.telemetry.model import (
    CpuUtilizationObservation,
    GpuObservation,
    SignalUnavailable,
    SwapObservation,
    SystemMemoryObservation,
    TelemetryMode,
    TelemetrySnapshot,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.telemetry.providers import GpuTelemetryProvider, MachineTelemetryProvider


@dataclass(frozen=True, slots=True)
class TelemetryConfiguration:
    """Selects signal groups, positive finite cadence and bounded history per claim."""

    memoryMode: TelemetryMode = TelemetryMode.CURRENT
    cpuMode: TelemetryMode = TelemetryMode.CURRENT
    gpuMode: TelemetryMode = TelemetryMode.CURRENT
    sampleIntervalSeconds: float = 1.0
    historySamples: int = 300

    def __post_init__(self) -> None:
        """Rejects invalid modes, non-finite cadence and non-positive retention."""
        for name in ("memoryMode", "cpuMode", "gpuMode"):
            if not isinstance(getattr(self, name), TelemetryMode):
                raise TypeError(f"{name} must be a TelemetryMode.")
        if type(self.sampleIntervalSeconds) not in {int, float}:
            raise ValueError("sampleIntervalSeconds must be finite and positive.")
        try:
            intervalNs = self.sampleIntervalSeconds * 1e9
        except OverflowError:
            raise ValueError("sampleIntervalSeconds must be finite and positive.") from None
        if not math.isfinite(intervalNs) or intervalNs < 1:
            raise ValueError("sampleIntervalSeconds must be finite and positive, representable in nanoseconds.")
        if type(self.historySamples) is not int or self.historySamples <= 0:
            raise ValueError("historySamples must be a positive exact integer.")

    @property
    def historyEnabled(self) -> bool:
        """Reports whether a group requests retained observations."""
        return TelemetryMode.HISTORY in {self.memoryMode, self.cpuMode, self.gpuMode}

    @property
    def enabled(self) -> bool:
        """Reports whether this claim needs any physical observations."""
        return any(mode is not TelemetryMode.DISABLED for mode in (self.memoryMode, self.cpuMode, self.gpuMode))


def _project(snapshot: TelemetrySnapshot, configuration: TelemetryConfiguration, *, history: bool) -> TelemetrySnapshot:
    """Masks unrequested groups while preserving the complete collection timing evidence."""
    memory, cpu, gpu = (
        mode is TelemetryMode.HISTORY if history else mode is not TelemetryMode.DISABLED
        for mode in (configuration.memoryMode, configuration.cpuMode, configuration.gpuMode)
    )
    return replace(
        snapshot,
        systemMemory=snapshot.systemMemory if memory else SignalUnavailable("memory disabled for this view"),
        swap=snapshot.swap if memory else SignalUnavailable("memory disabled for this view"),
        cpuStatic=snapshot.cpuStatic if cpu else SignalUnavailable("CPU disabled for this view"),
        cpuUtilization=snapshot.cpuUtilization if cpu else SignalUnavailable("CPU disabled for this view"),
        gpu=snapshot.gpu if gpu else SignalUnavailable("GPU disabled for this view"),
    )


class TelemetrySubscription:
    """An independent collection claim; release clears its data and is idempotent.

    The service serializes reads, delivery and release. Returned snapshots are immutable
    disposable evidence; previously returned values cannot be revoked by release.
    """

    def __init__(self, service: TelemetryService, configuration: TelemetryConfiguration) -> None:
        """Creates an unregistered claim; use service.subscribe() to acquire ownership."""
        self._service = service
        self.configuration = configuration
        self._current: TelemetrySnapshot | None = None
        self._history: deque[TelemetrySnapshot] = deque(maxlen=configuration.historySamples)
        self._nextDeliveryNs = 0
        self._released = False

    @property
    def current(self) -> TelemetrySnapshot | None:
        """Returns the last delivered observation without collecting hardware."""
        with self._service._condition:
            return self._current

    @property
    def history(self) -> tuple[TelemetrySnapshot, ...]:
        """Returns bounded immutable history with current-only groups masked."""
        with self._service._condition:
            return tuple(self._history)

    @property
    def released(self) -> bool:
        """Reports whether explicit release or owner teardown revoked this claim."""
        with self._service._condition:
            return self._released

    def release(self) -> None:
        """Revokes only this claim and clears its retained data, safely on repeated calls."""
        self._service._release(self)

    def __enter__(self) -> TelemetrySubscription:
        """Returns this claim for deterministic scoped release."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Releases this claim on every scope exit."""
        self.release()

    def _deliver(self, snapshot: TelemetrySnapshot, *, force: bool = False) -> None:
        """Downsamples shared reads without retaining intermediate observations."""
        if not force and snapshot.collectionStartNs < self._nextDeliveryNs:
            return
        self._current = _project(snapshot, self.configuration, history=False)
        if self.configuration.historyEnabled:
            self._history.append(_project(snapshot, self.configuration, history=True))
        self._nextDeliveryNs = snapshot.collectionStartNs + int(self.configuration.sampleIntervalSeconds * 1e9)


class TelemetryOwner:
    """Revocable owner scope without invocation context or application authority.

    PM-03 must validate authority before acquiring this scope. Explicit teardown,
    rather than garbage collection, releases every session created through it.
    """

    def __init__(self, service: TelemetryService) -> None:
        """Binds a lifetime scope to one host collector without starting collection."""
        self._service = service
        self._subscriptions: set[TelemetrySubscription] = set()
        self._released = False

    def subscribe(self, configuration: TelemetryConfiguration | None = None) -> TelemetrySubscription:
        """Acquires an independently releasable session unless this owner was revoked."""
        with self._service._condition:
            if self._released:
                raise RuntimeError("Telemetry owner has been released.")
            self._subscriptions = {item for item in self._subscriptions if not item.released}
            subscription = self._service.subscribe(configuration)
            self._subscriptions.add(subscription)
            return subscription

    def release(self) -> None:
        """Atomically revokes this owner and all of its remaining collection claims."""
        with self._service._condition:
            self._released = True
            for subscription in self._subscriptions:
                subscription.release()
            self._subscriptions.clear()

    def __enter__(self) -> TelemetryOwner:
        """Returns this explicitly managed ownership scope."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Releases owned claims on normal and exceptional scope exits."""
        self.release()


class TelemetryService:
    """Serializes one shared collector across legacy and optional monitoring claims.

    Injected providers are borrowed by monitoring. Factory-created providers are
    service owned and closed when unused. stop() relinquishes the legacy claim;
    it never revokes monitoring. Provider calls must finish promptly: release waits
    for any in-flight sample so no publication can occur after release returns.
    """

    def __init__(
        self, *, machineProvider: MachineTelemetryProvider | None,
        gpuProvider: GpuTelemetryProvider | None, configuration: TelemetryConfiguration | None = None,
        machineProviderFactory: Callable[[], MachineTelemetryProvider] | None = None,
        gpuProviderFactory: Callable[[], GpuTelemetryProvider] | None = None,
        monotonicNs: Callable[[], int] = time.monotonic_ns,
        wallTimeNs: Callable[[], int] = time.time_ns,
    ) -> None:
        """Binds available providers/factories without importing or activating optional hardware."""
        self.configuration = configuration or TelemetryConfiguration()
        self._machine = machineProvider
        self._gpu = gpuProvider
        self._machineFactory = machineProviderFactory
        self._gpuFactory = gpuProviderFactory
        self._ownedGpu = False
        self._machineAttempted = machineProvider is not None
        self._gpuAttempted = gpuProvider is not None
        self._monotonicNs = monotonicNs
        self._wallTimeNs = wallTimeNs
        self._condition = Condition(RLock())
        self._subscriptions: set[TelemetrySubscription] = set()
        self._history: deque[TelemetrySnapshot] = deque(maxlen=self.configuration.historySamples)
        self._current: TelemetrySnapshot | None = None
        self._latest: TelemetrySnapshot | None = None
        self._cpuStatic = None
        self._cpuPrimed = False
        self._thread: Thread | None = None
        self._started = False
        self._legacyNextDeliveryNs = 0
        self._closeBorrowedGpu = False

    @property
    def current(self) -> TelemetrySnapshot | None:
        """Returns legacy current data without initiating a provider read."""
        with self._condition:
            return self._current

    @property
    def history(self) -> tuple[TelemetrySnapshot, ...]:
        """Returns the legacy claim's bounded history, independent of monitoring retention."""
        with self._condition:
            return tuple(self._history)

    @property
    def collectionIntervalSeconds(self) -> float | None:
        """Returns the negotiated fastest polling interval, or None without polling claims."""
        with self._condition:
            cadences = [item.configuration.sampleIntervalSeconds for item in self._subscriptions
                        if item.configuration.enabled]
            if self._started and self.configuration.historyEnabled:
                cadences.append(self.configuration.sampleIntervalSeconds)
            return min(cadences) if cadences else None

    def owner(self) -> TelemetryOwner:
        """Creates an inactive revocable owner scope; authorization belongs to the caller."""
        return TelemetryOwner(self)

    def subscribe(self, configuration: TelemetryConfiguration | None = None) -> TelemetrySubscription:
        """Acquires a bounded monitoring claim and starts at most one shared polling worker.

        A joining claim receives the latest collected evidence when available; newly
        requested groups can remain unavailable until the next shared read. This does
        not trigger a duplicate hardware read just to initialize another consumer.
        Escaping setup failures revoke only the new claim and preserve the original
        exception. A later explicit acquisition can recover a failed polling worker.
        """
        with self._condition:
            subscription = TelemetrySubscription(self, configuration or TelemetryConfiguration())
            self._subscriptions.add(subscription)
            try:
                if self._latest is None:
                    self._collect()
                else:
                    subscription._deliver(self._latest, force=True)
                    subscription._nextDeliveryNs = 0
                self._ensureWorker()
            except BaseException as failure:
                try:
                    self._release(subscription)
                except BaseException as cleanupError:
                    failure.add_note(f"Telemetry acquisition rollback failed: {type(cleanupError).__name__}")
                raise
            return subscription

    def start(self) -> None:
        """Acquires the legacy claim, collects immediately and polls only for history mode."""
        with self._condition:
            if self._started:
                raise RuntimeError("TelemetryService is already started.")
            self._started = True
            self._legacyNextDeliveryNs = 0
            self._collect()
            self._ensureWorker()

    def sample(self) -> TelemetrySnapshot:
        """Reads once and explicitly requests polling recovery for remaining active claims.

        Successful collection is retained even if worker setup subsequently fails.
        Passive current/history reads never restart polling or restamp old evidence.
        """
        with self._condition:
            snapshot = self._collect(manual=True)
            self._ensureWorker()
            return snapshot

    def stop(self) -> None:
        """Releases legacy polling; provider closure waits until remaining monitoring is done."""
        with self._condition:
            self._started = False
            self._closeBorrowedGpu = True
            self._cleanup()
            self._condition.notify_all()

    def _release(self, subscription: TelemetrySubscription) -> None:
        """Serializes revocation with collection and removes only this subscription's data."""
        with self._condition:
            subscription._released = True
            subscription._current = None
            subscription._history.clear()
            self._subscriptions.discard(subscription)
            self._cleanup()
            self._condition.notify_all()

    def _configurations(self, *, manual: bool = False) -> list[TelemetryConfiguration]:
        """Returns active signal requirements; explicit legacy reads also use legacy settings."""
        configurations = [item.configuration for item in self._subscriptions]
        if self._started or manual:
            configurations.append(self.configuration)
        return configurations

    def _cleanup(self) -> None:
        """Releases only service-owned optional resources and disposable monitoring evidence."""
        configurations = self._configurations()
        gpuEnabled = any(item.gpuMode is not TelemetryMode.DISABLED for item in configurations)
        if self._gpu is not None and ((self._ownedGpu and not gpuEnabled)
                                     or (self._closeBorrowedGpu and not configurations)):
            try:
                self._gpu.close()
            except Exception:
                pass  # Optional cleanup cannot revoke another consumer or fail its owner teardown.
            self._gpu = None
            self._gpuAttempted = False
            self._ownedGpu = False
            self._closeBorrowedGpu = False
        if not any(item.cpuMode is not TelemetryMode.DISABLED for item in configurations):
            self._cpuPrimed = False
            self._cpuStatic = None
        machineEnabled = any(
            item.memoryMode is not TelemetryMode.DISABLED or item.cpuMode is not TelemetryMode.DISABLED
            for item in configurations
        )
        if not machineEnabled and self._machineFactory is not None:
            self._machine = None
            self._machineAttempted = False
        if not gpuEnabled:
            self._gpuAttempted = self._gpu is not None
        if not any(item.enabled for item in configurations):
            self._latest = None

    def _ensureWorker(self) -> None:
        """Publishes one worker under the lifecycle lock and rolls back failed startup.

        A worker that starts before start() raises sees its revoked identity and
        exits without collecting. Existing workers remain the sole collector.
        """
        if self.collectionIntervalSeconds is not None and self._thread is None:
            worker = Thread(target=self._samplingLoop, name="actant-telemetry", daemon=True)
            self._thread = worker
            try:
                worker.start()
            except BaseException:
                if self._thread is worker:
                    self._thread = None
                self._condition.notify_all()
                raise
        self._condition.notify_all()

    def _samplingLoop(self) -> None:
        """Polls monotonic deadlines and relinquishes only this worker's identity on every exit.

        Unexpected failures propagate to threading.excepthook, without automatic
        retries. A later subscribe/start/sample operation may request one worker.
        Stale samples retain their original timestamps throughout interruption.
        """
        worker = current_thread()
        with self._condition:
            try:
                while self._thread is worker and (interval := self.collectionIntervalSeconds) is not None:
                    deadline = 0
                    if self._latest is not None:
                        deadline = self._latest.collectionStartNs + int(interval * 1e9)
                        if deadline <= self._latest.collectionEndNs:
                            deadline = self._latest.collectionEndNs + int(interval * 1e9)
                    delay = (deadline - self._monotonicNs()) / 1e9
                    if delay > 0:
                        self._condition.wait(timeout=min(delay, 86400.0))
                        continue
                    self._collect()
            finally:
                if self._thread is worker:
                    self._thread = None
                self._condition.notify_all()

    def _prepareProviders(self, *, machine: bool, cpu: bool, gpu: bool) -> None:
        """Lazily binds only requested groups and primes CPU once per active CPU lifetime."""
        if machine and not self._machineAttempted and self._machineFactory is not None:
            self._machineAttempted = True
            try:
                self._machine = self._machineFactory()
            except Exception:
                self._machine = None
        if gpu and not self._gpuAttempted and self._gpuFactory is not None:
            self._gpuAttempted = True
            try:
                self._gpu = self._gpuFactory()
                self._ownedGpu = True
            except Exception:
                self._gpu = None
        if cpu and self._machine is not None and self._cpuStatic is None:
            try:
                self._machine.primeCpu()
                self._cpuPrimed = True
                self._cpuStatic = self._machine.cpuStatic()
            except Exception as err:
                self._cpuStatic = SignalUnavailable(f"CPU preparation unavailable: {type(err).__name__}")

    def _collect(self, *, manual: bool = False) -> TelemetrySnapshot:
        """Collects sequential enabled groups under the lifecycle lock and publishes bounded projections."""
        start = self._monotonicNs()
        wall = self._wallTimeNs()
        configurations = self._configurations(manual=manual)
        memory, cpu, gpu = (any(getattr(item, name) is not TelemetryMode.DISABLED for item in configurations)
                            for name in ("memoryMode", "cpuMode", "gpuMode"))
        self._prepareProviders(machine=memory or cpu, cpu=cpu, gpu=gpu)
        if not cpu:
            cpuUtilization = SignalUnavailable("CPU disabled")
        elif not self._cpuPrimed:
            cpuUtilization = SignalUnavailable("CPU utilization counters were not primed")
        else:
            cpuUtilization = self._readCpuUtilization()
        snapshot = TelemetrySnapshot(
            sampledTimeNs=wall,
            systemMemory=self._readSystemMemory() if memory else SignalUnavailable("memory disabled"),
            swap=self._readSwap() if memory else SignalUnavailable("memory disabled"),
            cpuStatic=(
                (self._cpuStatic or SignalUnavailable("CPU topology unavailable"))
                if cpu else SignalUnavailable("CPU disabled")
            ),
            cpuUtilization=cpuUtilization,
            gpu=self._readGpu() if gpu else SignalUnavailable("GPU disabled"),
            collectionStartNs=start,
            collectionEndNs=self._monotonicNs(),
            sampleIntervalSeconds=self.collectionIntervalSeconds,
            actualIntervalSeconds=(start - self._latest.collectionStartNs) / 1e9 if self._latest else None,
        )
        self._latest = snapshot
        if self._started or manual:
            self._current = _project(snapshot, self.configuration, history=False)
            if self.configuration.historyEnabled and (manual or start >= self._legacyNextDeliveryNs):
                self._history.append(_project(snapshot, self.configuration, history=True))
                self._legacyNextDeliveryNs = start + int(self.configuration.sampleIntervalSeconds * 1e9)
        for subscription in self._subscriptions:
            subscription._deliver(snapshot)
        return _project(snapshot, self.configuration, history=False) if manual else snapshot

    def _readSystemMemory(self) -> SystemMemoryObservation | SignalUnavailable:
        """Reads physical-memory telemetry while preserving provider failure as unavailable."""
        if self._machine is None:
            return SignalUnavailable("machine telemetry provider unavailable")
        try:
            return self._machine.systemMemory()
        except Exception as err:
            return SignalUnavailable(f"systemMemory unavailable: {type(err).__name__}")

    def _readSwap(self) -> SwapObservation | SignalUnavailable:
        """Reads swap/pagefile telemetry while preserving provider failure as unavailable."""
        if self._machine is None:
            return SignalUnavailable("machine telemetry provider unavailable")
        try:
            return self._machine.swap()
        except Exception as err:
            return SignalUnavailable(f"swap unavailable: {type(err).__name__}")

    def _readCpuUtilization(self) -> CpuUtilizationObservation | SignalUnavailable:
        """Reads CPU utilization while preserving provider failure as unavailable."""
        if self._machine is None:
            return SignalUnavailable("machine telemetry provider unavailable")
        try:
            return self._machine.cpuUtilization()
        except Exception as err:
            return SignalUnavailable(f"cpuUtilization unavailable: {type(err).__name__}")

    def _readGpu(self) -> GpuObservation | SignalUnavailable:
        """Reads GPU telemetry and maps absent/failing providers to unavailable."""
        if self._gpu is None:
            return SignalUnavailable("GPU telemetry provider unavailable")
        try:
            return self._gpu.gpu()
        except Exception as err:
            return SignalUnavailable(f"GPU telemetry unavailable: {type(err).__name__}")
