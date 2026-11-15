# file: backend/telemetry/model.py ; version: 2
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TelemetryMode(StrEnum):
    """Selects whether a telemetry group is inactive, current-only, or retained."""

    DISABLED = "disabled"
    CURRENT = "current"
    HISTORY = "history"


@dataclass(frozen=True, slots=True)
class SignalUnavailable:
    """Describes an unavailable observation without fabricating a numeric zero."""

    reason: str

    def __post_init__(self) -> None:
        """Requires a useful diagnostic reason."""
        if type(self.reason) is not str or not self.reason.strip():
            raise ValueError("SignalUnavailable.reason must be a non-empty string.")


type ObservedInt = int | SignalUnavailable
type ObservedFloat = float | SignalUnavailable


@dataclass(frozen=True, slots=True)
class SystemMemoryObservation:
    """Captures provider-native physical-memory values in bytes and percent."""

    totalBytes: int
    availableBytes: int
    usedBytes: int
    freeBytes: int
    usagePercent: float


@dataclass(frozen=True, slots=True)
class SwapObservation:
    """Captures provider-native swap/pagefile capacity values."""

    totalBytes: int
    usedBytes: int
    freeBytes: int
    usagePercent: float


@dataclass(frozen=True, slots=True)
class CpuStaticObservation:
    """Captures CPU topology which normally remains static for a host lifetime."""

    physicalCores: ObservedInt
    logicalCores: ObservedInt


@dataclass(frozen=True, slots=True)
class CpuUtilizationObservation:
    """Captures aggregate and per-logical-core CPU utilization percentages."""

    aggregatePercent: float
    perLogicalCorePercent: tuple[float, ...]

    @property
    def busiestLogicalCorePercent(self) -> float:
        """Returns the highest observed logical-core utilization."""
        return max(self.perLogicalCorePercent, default=0.0)


@dataclass(frozen=True, slots=True)
class GpuObservation:
    """Captures one NVIDIA device without conflating capacity and controller load."""

    deviceIndex: int
    name: str
    totalVramBytes: int
    usedVramBytes: int
    freeVramBytes: int
    gpuUtilizationPercent: float
    memoryControllerUtilizationPercent: float
    temperatureCelsius: ObservedFloat
    powerDrawWatts: ObservedFloat
    powerLimitWatts: ObservedFloat
    graphicsClockMhz: ObservedInt
    memoryClockMhz: ObservedInt
    performanceState: str | SignalUnavailable
    fanSpeedPercent: ObservedFloat


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    """Represents sequential host reads, including lazy setup, over a monotonic interval.

    sampledTimeNs is wall-clock evidence captured immediately after collectionStartNs;
    it is never the clock for alignment or rates. sampleIntervalSeconds is the
    negotiated polling target; actualIntervalSeconds is spacing from the preceding
    shared collection start (None for the first). Reads are not simultaneous, and
    delayed collection/gaps remain visible in start/end and actual spacing.
    Missing timing on externally constructed snapshots is explicitly None.
    """

    sampledTimeNs: int
    systemMemory: SystemMemoryObservation | SignalUnavailable
    swap: SwapObservation | SignalUnavailable
    cpuStatic: CpuStaticObservation | SignalUnavailable
    cpuUtilization: CpuUtilizationObservation | SignalUnavailable
    gpu: GpuObservation | SignalUnavailable

    # sampledTimeNs remains wall-clock display evidence, never a rate/alignment clock.
    collectionStartNs: int | None = None
    collectionEndNs: int | None = None
    sampleIntervalSeconds: float | None = None
    actualIntervalSeconds: float | None = None
