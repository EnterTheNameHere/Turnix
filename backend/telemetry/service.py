# file: backend/telemetry/service.py ; version: 3
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from threading import Event, RLock, Thread

from backend.telemetry.model import SignalUnavailable, TelemetryMode, TelemetrySnapshot
from backend.telemetry.providers import GpuTelemetryProvider, MachineTelemetryProvider


@dataclass(frozen=True, slots=True)
class TelemetryConfiguration:
    """Configures independent telemetry groups and optional bounded sampling history."""

    memoryMode: TelemetryMode = TelemetryMode.CURRENT
    cpuMode: TelemetryMode = TelemetryMode.CURRENT
    gpuMode: TelemetryMode = TelemetryMode.CURRENT
    sampleIntervalSeconds: float = 1.0
    historySamples: int = 300

    def __post_init__(self) -> None:
        """Validates cadence and bounded-retention configuration."""
        for name in ("memoryMode", "cpuMode", "gpuMode"):
            if not isinstance(getattr(self, name), TelemetryMode):
                raise TypeError(f"{name} must be a TelemetryMode.")
        if type(self.sampleIntervalSeconds) not in {int, float} or self.sampleIntervalSeconds <= 0:
            raise ValueError("sampleIntervalSeconds must be positive.")
        if type(self.historySamples) is not int or self.historySamples <= 0:
            raise ValueError("historySamples must be a positive exact integer.")

    @property
    def historyEnabled(self) -> bool:
        """Reports whether any dynamic signal group requests retained samples."""
        return TelemetryMode.HISTORY in {self.memoryMode, self.cpuMode, self.gpuMode}


class TelemetryService:
    """Owns current host observations and disposable bounded sampling history.

    The service contains no resource-policy thresholds. Provider failures become
    explicit unavailable observations, and retained samples never become Actant
    authoritative State or transactional Memory.
    """

    def __init__(
        self,
        *,
        machineProvider: MachineTelemetryProvider | None,
        gpuProvider: GpuTelemetryProvider | None,
        configuration: TelemetryConfiguration | None = None,
    ) -> None:
        """Creates a sampler around independently optional machine and GPU providers."""
        self.configuration = configuration or TelemetryConfiguration()
        self._machine = machineProvider
        self._gpu = gpuProvider
        self._lane = RLock()
        self._history: deque[TelemetrySnapshot] = deque(maxlen=self.configuration.historySamples)
        self._current: TelemetrySnapshot | None = None
        self._cpuStatic = None
        self._cpuPrimed = False
        self._stopEvent = Event()
        self._thread: Thread | None = None
        self._started = False

    @property
    def current(self) -> TelemetrySnapshot | None:
        """Returns the most recent snapshot without initiating a provider read."""
        with self._lane:
            return self._current

    @property
    def history(self) -> tuple[TelemetrySnapshot, ...]:
        """Returns retained samples with current-only groups deliberately omitted."""
        with self._lane:
            return tuple(self._history)

    def start(self) -> None:
        """Primes CPU counters, captures static topology once, and starts cadence when needed."""
        with self._lane:
            if self._started:
                raise RuntimeError("TelemetryService is already started.")
            self._started = True
            if self._machine is not None and self.configuration.cpuMode is not TelemetryMode.DISABLED:
                try:
                    self._machine.primeCpu()
                    self._cpuPrimed = True
                except Exception:
                    self._cpuPrimed = False
                try:
                    self._cpuStatic = self._machine.cpuStatic()
                except Exception as err:
                    self._cpuStatic = SignalUnavailable(f"CPU topology unavailable: {type(err).__name__}")
            self.sample()
            if self.configuration.historyEnabled:
                self._thread = Thread(target=self._samplingLoop, name="actant-telemetry", daemon=True)
                self._thread.start()

    def sample(self) -> TelemetrySnapshot:
        """Collects one non-blocking observation, updating current value and enabled histories."""
        with self._lane:
            memoryEnabled = self.configuration.memoryMode is not TelemetryMode.DISABLED
            cpuEnabled = self.configuration.cpuMode is not TelemetryMode.DISABLED
            gpuEnabled = self.configuration.gpuMode is not TelemetryMode.DISABLED
            if not cpuEnabled:
                cpuUtilization = SignalUnavailable("CPU disabled")
            elif not self._cpuPrimed:
                cpuUtilization = SignalUnavailable("CPU utilization counters were not primed")
            else:
                cpuUtilization = self._readMachine("cpuUtilization")
            snapshot = TelemetrySnapshot(
                sampledTimeNs=time.time_ns(),
                systemMemory=self._readMachine("systemMemory") if memoryEnabled else SignalUnavailable("memory disabled"),
                swap=self._readMachine("swap") if memoryEnabled else SignalUnavailable("memory disabled"),
                cpuStatic=(self._cpuStatic or SignalUnavailable("CPU topology unavailable"))
                if cpuEnabled else SignalUnavailable("CPU disabled"),
                cpuUtilization=cpuUtilization,
                gpu=self._readGpu() if gpuEnabled else SignalUnavailable("GPU disabled"),
            )
            self._current = snapshot
            if self.configuration.historyEnabled:
                self._history.append(self._forHistory(snapshot))
            return snapshot

    def stop(self) -> None:
        """Stops background sampling and releases optional provider resources."""
        self._stopEvent.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self.configuration.sampleIntervalSeconds * 2.0 + 1.0)
        self._thread = None
        if self._gpu is not None:
            try:
                self._gpu.close()
            except Exception:
                pass

    def _samplingLoop(self) -> None:
        """Samples at the configured cadence until shutdown without blocking signal reads."""
        while not self._stopEvent.wait(self.configuration.sampleIntervalSeconds):
            self.sample()

    def _forHistory(self, snapshot: TelemetrySnapshot) -> TelemetrySnapshot:
        """Masks groups which requested current values but not historical retention."""
        memoryHistory = self.configuration.memoryMode is TelemetryMode.HISTORY
        cpuHistory = self.configuration.cpuMode is TelemetryMode.HISTORY
        gpuHistory = self.configuration.gpuMode is TelemetryMode.HISTORY
        return TelemetrySnapshot(
            sampledTimeNs=snapshot.sampledTimeNs,
            systemMemory=snapshot.systemMemory if memoryHistory else SignalUnavailable("memory history disabled"),
            swap=snapshot.swap if memoryHistory else SignalUnavailable("memory history disabled"),
            cpuStatic=snapshot.cpuStatic if cpuHistory else SignalUnavailable("CPU history disabled"),
            cpuUtilization=snapshot.cpuUtilization if cpuHistory else SignalUnavailable("CPU history disabled"),
            gpu=snapshot.gpu if gpuHistory else SignalUnavailable("GPU history disabled"),
        )

    def _readMachine(self, operationName: str):
        """Reads one machine-provider operation and maps provider failure to unavailable."""
        if self._machine is None:
            return SignalUnavailable("machine telemetry provider unavailable")
        try:
            return getattr(self._machine, operationName)()
        except Exception as err:
            return SignalUnavailable(f"{operationName} unavailable: {type(err).__name__}")

    def _readGpu(self):
        """Reads GPU telemetry and maps absent/failing providers to unavailable."""
        if self._gpu is None:
            return SignalUnavailable("GPU telemetry provider unavailable")
        try:
            return self._gpu.gpu()
        except Exception as err:
            return SignalUnavailable(f"GPU telemetry unavailable: {type(err).__name__}")
