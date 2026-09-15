# file: backend/telemetry/providers.py ; version: 1
from __future__ import annotations

from typing import Protocol

from backend.telemetry.model import (
    CpuStaticObservation,
    CpuUtilizationObservation,
    GpuObservation,
    SignalUnavailable,
    SwapObservation,
    SystemMemoryObservation,
)


class MachineTelemetryProvider(Protocol):
    """Defines host CPU and memory observations required by the telemetry service."""

    def primeCpu(self) -> None:
        """Primes non-blocking CPU counters without publishing their first result."""
        ...

    def systemMemory(self) -> SystemMemoryObservation:
        """Returns current physical-memory values."""
        ...

    def swap(self) -> SwapObservation:
        """Returns current swap/pagefile values."""
        ...

    def cpuStatic(self) -> CpuStaticObservation:
        """Returns physical and logical CPU topology."""
        ...

    def cpuUtilization(self) -> CpuUtilizationObservation:
        """Returns non-blocking aggregate and per-logical-core utilization."""
        ...


class GpuTelemetryProvider(Protocol):
    """Defines observation of the currently relevant GPU device."""

    def gpu(self) -> GpuObservation:
        """Returns one current GPU observation."""
        ...

    def close(self) -> None:
        """Releases provider resources when applicable."""
        ...


class PsutilMachineTelemetryProvider:
    """Maps psutil host observations without redefining provider memory semantics."""

    def __init__(self, psutilModule: object | None = None) -> None:
        """Binds to psutil, permitting an injected module seam for deterministic tests."""
        if psutilModule is None:
            import psutil as psutilModule  # type: ignore[import-not-found]
        self._psutil = psutilModule

    def primeCpu(self) -> None:
        """Primes both aggregate and per-core psutil CPU percentage counters."""
        self._psutil.cpu_percent(interval=None, percpu=False)
        self._psutil.cpu_percent(interval=None, percpu=True)

    def systemMemory(self) -> SystemMemoryObservation:
        """Preserves psutil virtual-memory total, available, used, free, and percent."""
        value = self._psutil.virtual_memory()
        return SystemMemoryObservation(
            totalBytes=int(value.total),
            availableBytes=int(value.available),
            usedBytes=int(value.used),
            freeBytes=int(value.free),
            usagePercent=float(value.percent),
        )

    def swap(self) -> SwapObservation:
        """Preserves psutil swap/pagefile capacity values without claiming paging rate."""
        value = self._psutil.swap_memory()
        return SwapObservation(
            totalBytes=int(value.total),
            usedBytes=int(value.used),
            freeBytes=int(value.free),
            usagePercent=float(value.percent),
        )

    def cpuStatic(self) -> CpuStaticObservation:
        """Returns psutil physical/logical counts while representing unknown counts explicitly."""
        physical = self._psutil.cpu_count(logical=False)
        logical = self._psutil.cpu_count(logical=True)
        return CpuStaticObservation(
            physicalCores=(
                int(physical) if physical is not None else SignalUnavailable("physical CPU count unavailable")
            ),
            logicalCores=(
                int(logical) if logical is not None else SignalUnavailable("logical CPU count unavailable")
            ),
        )

    def cpuUtilization(self) -> CpuUtilizationObservation:
        """Returns already-primed non-blocking CPU utilization observations."""
        aggregate = float(self._psutil.cpu_percent(interval=None, percpu=False))
        perCore = tuple(float(value) for value in self._psutil.cpu_percent(interval=None, percpu=True))
        return CpuUtilizationObservation(
            aggregatePercent=aggregate,
            perLogicalCorePercent=perCore,
        )


class NvmlGpuTelemetryProvider:
    """Observes one NVIDIA GPU through NVML while degrading optional sensors independently."""

    def __init__(self, *, deviceIndex: int = 0, nvmlModule: object | None = None) -> None:
        """Initializes NVML and selects one device by index."""
        if type(deviceIndex) is not int or deviceIndex < 0:
            raise ValueError("deviceIndex must be a non-negative exact integer.")
        if nvmlModule is None:
            import pynvml as nvmlModule  # type: ignore[import-not-found]
        self._nvml = nvmlModule
        self._nvml.nvmlInit()
        try:
            self._handle = self._nvml.nvmlDeviceGetHandleByIndex(deviceIndex)
        except Exception:
            self._nvml.nvmlShutdown()
            raise
        self.deviceIndex = deviceIndex
        self._closed = False

    def _optional(self, operationName: str, transform) -> object:
        """Reads an optional NVML sensor and converts unsupported/failing reads to unavailable."""
        try:
            return transform(getattr(self._nvml, operationName)(self._handle))
        except Exception as err:
            return SignalUnavailable(f"{operationName} unavailable: {type(err).__name__}")

    def gpu(self) -> GpuObservation:
        """Returns required GPU memory/utilization plus best-effort diagnostic sensors."""
        memory = self._nvml.nvmlDeviceGetMemoryInfo(self._handle)
        utilization = self._nvml.nvmlDeviceGetUtilizationRates(self._handle)
        name = self._nvml.nvmlDeviceGetName(self._handle)
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")
        return GpuObservation(
            deviceIndex=self.deviceIndex,
            name=str(name),
            totalVramBytes=int(memory.total),
            usedVramBytes=int(memory.used),
            freeVramBytes=int(memory.free),
            gpuUtilizationPercent=float(utilization.gpu),
            memoryControllerUtilizationPercent=float(utilization.memory),
            temperatureCelsius=self._optional(
                "nvmlDeviceGetTemperature",
                float,
            ),
            powerDrawWatts=self._optional("nvmlDeviceGetPowerUsage", lambda value: float(value) / 1000.0),
            powerLimitWatts=self._optional(
                "nvmlDeviceGetEnforcedPowerLimit",
                lambda value: float(value) / 1000.0,
            ),
            graphicsClockMhz=self._optional(
                "nvmlDeviceGetClockInfo",
                int,
            ),
            memoryClockMhz=self._optional(
                "nvmlDeviceGetMemoryInfo",
                lambda _value: int(self._nvml.nvmlDeviceGetClockInfo(self._handle, 2)),
            ),
            performanceState=self._optional("nvmlDeviceGetPerformanceState", lambda value: str(value)),
            fanSpeedPercent=self._optional("nvmlDeviceGetFanSpeed", float),
        )

    def close(self) -> None:
        """Shuts down this provider's NVML session once."""
        if self._closed:
            return
        self._closed = True
        self._nvml.nvmlShutdown()
