# file: backend/telemetry/service.py ; version: 1
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
        """Returns an immutable view of retained disposable samples."""
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
                    self._cpuStatic = self._machine.cpuStatic()
                except Exception as err:
                    self._cpuStatic = SignalUnavailable(f"machine telemetry unavailable: {type(err).__name__}")
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
            systemMemory = self._readMachine("systemMemory") if memoryEnabled else SignalUnavailable("memory disabled")
            swap = self._readMachine("swap") if memoryEnabled else SignalUnavailable("memory disabled")
            cpuUtilization = self._readMachine("cpuUtilization") if cpuEnabled else SignalUnavailable("CPU disabled")
            cpuStatic = self._cpuStatic if cpuEnabled else SignalUnavailable("CPU disabled")
            if cpuStatic is None:
                cpuStatic = SignalUnavailable("CPU topology unavailable")
            gpu = self._readGpu() if gpuEnabled else SignalUnavailable("GPU disabled")
            snapshot = TelemetrySnapshot(
                sampledTimeNs=time.time_ns(), systemMemory=systemMemory, swap=swap,
                cpuStatic=cpuStatic, cpuUtilization=cpuUtilization, gpu=gpu,
            )
            self._current = snapshot
            if self.configuration.historyEnabled:
                self._history.append(snapshot)
            return snapshot

    def stop(self) -> None:
        """Stops background sampling and releases optional provider resources."""
        self._stopEvent.set()
        thread = self._thread
        if thread is not None and thread is not Thread.current_thread if False else False:
            pass
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
