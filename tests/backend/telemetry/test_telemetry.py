# file: tests/backend/telemetry/test_telemetry.py ; version: 1
from __future__ import annotations

from types import SimpleNamespace

from backend.telemetry import (
    CpuStaticObservation,
    CpuUtilizationObservation,
    GpuObservation,
    SignalUnavailable,
    SwapObservation,
    SystemMemoryObservation,
    TelemetryConfiguration,
    TelemetryMode,
    TelemetryService,
)
from backend.telemetry.providers import NvmlGpuTelemetryProvider, PsutilMachineTelemetryProvider


class FakePsutil:
    """Provides deterministic psutil-compatible values and records CPU priming calls."""

    def __init__(self) -> None:
        """Initializes deterministic host values and call evidence."""
        self.cpuCalls: list[bool] = []

    def virtual_memory(self):
        """Returns deliberately non-derived physical-memory values."""
        return SimpleNamespace(total=1000, available=700, used=250, free=100, percent=25.0)

    def swap_memory(self):
        """Returns deterministic pagefile capacity values."""
        return SimpleNamespace(total=500, used=125, free=375, percent=25.0)

    def cpu_count(self, *, logical: bool):
        """Returns distinct physical and logical topology counts."""
        return 12 if logical else 6

    def cpu_percent(self, *, interval, percpu: bool):
        """Returns priming zeros first and useful observations thereafter."""
        del interval
        self.cpuCalls.append(percpu)
        occurrence = self.cpuCalls.count(percpu)
        if occurrence == 1:
            return [0.0, 0.0] if percpu else 0.0
        return [11.0, 97.0] if percpu else 54.0


class FakeMachineProvider:
    """Provides stable machine observations while counting static topology reads."""

    def __init__(self) -> None:
        """Initializes counters for static-read verification."""
        self.staticReads = 0
        self.primed = False

    def primeCpu(self) -> None:
        """Marks CPU utilization as primed."""
        self.primed = True

    def systemMemory(self) -> SystemMemoryObservation:
        """Returns stable memory telemetry."""
        return SystemMemoryObservation(100, 60, 35, 20, 35.0)

    def swap(self) -> SwapObservation:
        """Returns stable swap telemetry."""
        return SwapObservation(20, 5, 15, 25.0)

    def cpuStatic(self) -> CpuStaticObservation:
        """Returns topology and records that static data was read."""
        self.staticReads += 1
        return CpuStaticObservation(physicalCores=4, logicalCores=8)

    def cpuUtilization(self) -> CpuUtilizationObservation:
        """Requires priming and returns one intentionally saturated logical core."""
        assert self.primed
        return CpuUtilizationObservation(aggregatePercent=40.0, perLogicalCorePercent=(20.0, 99.0, 30.0))


class FakeGpuProvider:
    """Provides GPU capacity and utilization values with deliberately distinct percentages."""

    def __init__(self) -> None:
        """Initializes close-state evidence."""
        self.closed = False

    def gpu(self) -> GpuObservation:
        """Returns deterministic GPU telemetry."""
        unavailable = SignalUnavailable("optional sensor unsupported")
        return GpuObservation(
            deviceIndex=0, name="GPU", totalVramBytes=16000, usedVramBytes=12000,
            freeVramBytes=4000, gpuUtilizationPercent=91.0,
            memoryControllerUtilizationPercent=37.0, temperatureCelsius=unavailable,
            powerDrawWatts=unavailable, powerLimitWatts=unavailable,
            graphicsClockMhz=unavailable, memoryClockMhz=unavailable,
            performanceState=unavailable, fanSpeedPercent=unavailable,
        )

    def close(self) -> None:
        """Records provider cleanup."""
        self.closed = True


class FakeNvml:
    """Provides the required NVML surface while rejecting optional sensors."""

    NVML_TEMPERATURE_GPU = 0
    NVML_CLOCK_GRAPHICS = 0
    NVML_CLOCK_MEM = 2

    def __init__(self) -> None:
        """Initializes lifecycle evidence."""
        self.shutdown = False

    def nvmlInit(self) -> None:
        """Accepts initialization."""

    def nvmlShutdown(self) -> None:
        """Records shutdown."""
        self.shutdown = True

    def nvmlDeviceGetHandleByIndex(self, index: int):
        """Returns a deterministic opaque handle."""
        return f"gpu-{index}"

    def nvmlDeviceGetMemoryInfo(self, handle):
        """Returns exact VRAM capacity values."""
        del handle
        return SimpleNamespace(total=1000, used=600, free=400)

    def nvmlDeviceGetUtilizationRates(self, handle):
        """Returns distinct compute and memory-controller utilization."""
        del handle
        return SimpleNamespace(gpu=88, memory=33)

    def nvmlDeviceGetName(self, handle):
        """Returns a byte-string name like some NVML versions."""
        del handle
        return b"Test GPU"

    def __getattr__(self, name: str):
        """Makes every optional sensor explicitly unsupported."""
        raise RuntimeError(f"unsupported {name}")


def test_psutil_mapping_preserves_provider_memory_values_and_primes_cpu() -> None:
    """Provider mapping keeps units/semantics and discards the meaningless first CPU result."""
    fake = FakePsutil()
    provider = PsutilMachineTelemetryProvider(fake)
    provider.primeCpu()
    memory = provider.systemMemory()
    swap = provider.swap()
    cpu = provider.cpuUtilization()
    assert (memory.totalBytes, memory.availableBytes, memory.usedBytes, memory.freeBytes) == (1000, 700, 250, 100)
    assert (swap.totalBytes, swap.usedBytes, swap.freeBytes) == (500, 125, 375)
    assert cpu.aggregatePercent == 54.0
    assert cpu.perLogicalCorePercent == (11.0, 97.0)
    assert cpu.busiestLogicalCorePercent == 97.0


def test_nvml_mapping_keeps_capacity_and_controller_utilization_distinct() -> None:
    """NVML mapping never confuses allocated VRAM percentage with controller activity."""
    fake = FakeNvml()
    provider = NvmlGpuTelemetryProvider(nvmlModule=fake)
    gpu = provider.gpu()
    assert (gpu.totalVramBytes, gpu.usedVramBytes, gpu.freeVramBytes) == (1000, 600, 400)
    assert gpu.gpuUtilizationPercent == 88.0
    assert gpu.memoryControllerUtilizationPercent == 33.0
    assert isinstance(gpu.temperatureCelsius, SignalUnavailable)
    provider.close()
    assert fake.shutdown


def test_service_modes_history_bounds_and_static_topology() -> None:
    """Current-only groups remain current-only while history is bounded and topology is cached."""
    machine = FakeMachineProvider()
    gpu = FakeGpuProvider()
    service = TelemetryService(
        machineProvider=machine,
        gpuProvider=gpu,
        configuration=TelemetryConfiguration(
            memoryMode=TelemetryMode.CURRENT,
            cpuMode=TelemetryMode.HISTORY,
            gpuMode=TelemetryMode.DISABLED,
            sampleIntervalSeconds=3600.0,
            historySamples=2,
        ),
    )
    service.start()
    service.sample()
    current = service.sample()
    assert machine.staticReads == 1
    assert current.cpuUtilization.busiestLogicalCorePercent == 99.0
    assert isinstance(current.gpu, SignalUnavailable)
    assert len(service.history) == 2
    assert isinstance(service.history[-1].systemMemory, SignalUnavailable)
    assert isinstance(service.history[-1].cpuUtilization, CpuUtilizationObservation)
    service.stop()
    assert gpu.closed


def test_provider_failure_is_unavailable_not_zero() -> None:
    """Absent optional providers produce explicit unavailable observations rather than fake zeros."""
    service = TelemetryService(
        machineProvider=None,
        gpuProvider=None,
        configuration=TelemetryConfiguration(),
    )
    service.start()
    snapshot = service.current
    assert snapshot is not None
    assert isinstance(snapshot.systemMemory, SignalUnavailable)
    assert isinstance(snapshot.cpuUtilization, SignalUnavailable)
    assert isinstance(snapshot.gpu, SignalUnavailable)
    service.stop()
