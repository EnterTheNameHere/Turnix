# file: backend/telemetry/__init__.py ; version: 2
from __future__ import annotations

import pynvml

from backend.telemetry.model import (
    CpuStaticObservation,
    CpuUtilizationObservation,
    GpuObservation,
    SignalUnavailable,
    SwapObservation,
    SystemMemoryObservation,
    TelemetryMode,
    TelemetrySnapshot,
)
from backend.telemetry.providers import NvmlGpuTelemetryProvider, PsutilMachineTelemetryProvider
from backend.telemetry.service import TelemetryConfiguration, TelemetryService

__all__ = [
    "CpuStaticObservation",
    "CpuUtilizationObservation",
    "GpuObservation",
    "SignalUnavailable",
    "SwapObservation",
    "SystemMemoryObservation",
    "TelemetryConfiguration",
    "TelemetryMode",
    "TelemetryService",
    "TelemetrySnapshot",
    "createDefaultTelemetryService",
]


def createDefaultTelemetryService(
    configuration: TelemetryConfiguration | None = None,
) -> TelemetryService:
    """Creates Windows-first host telemetry while treating unavailable providers as optional."""
    try:
        machineProvider = PsutilMachineTelemetryProvider()
    except (ImportError, OSError, RuntimeError):
        machineProvider = None
    try:
        gpuProvider = NvmlGpuTelemetryProvider()
    except (ImportError, OSError, RuntimeError, ValueError, pynvml.NVMLError):
        gpuProvider = None
    return TelemetryService(
        machineProvider=machineProvider,
        gpuProvider=gpuProvider,
        configuration=configuration,
    )
