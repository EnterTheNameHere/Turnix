# file: backend/telemetry/model.py ; version: 1
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
    """Represents one immutable, non-authoritative host observation point."""

    sampledTimeNs: int
    systemMemory: SystemMemoryObservation | SignalUnavailable
    swap: SwapObservation | SignalUnavailable
    cpuStatic: CpuStaticObservation | SignalUnavailable
    cpuUtilization: CpuUtilizationObservation | SignalUnavailable
    gpu: GpuObservation | SignalUnavailable
