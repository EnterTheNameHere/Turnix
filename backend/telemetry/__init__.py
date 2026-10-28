# file: backend/telemetry/__init__.py ; version: 3
from __future__ import annotations

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
from backend.telemetry.service import TelemetryConfiguration, TelemetryOwner, TelemetryService, TelemetrySubscription

__all__ = [
    "CpuStaticObservation",
    "CpuUtilizationObservation",
    "GpuObservation",
    "SignalUnavailable",
    "SwapObservation",
    "SystemMemoryObservation",
    "TelemetryConfiguration",
    "TelemetryMode",
    "TelemetryOwner",
    "TelemetrySubscription",
    "TelemetryService",
    "TelemetrySnapshot",
    "createDefaultTelemetryService",
]


def createDefaultTelemetryService(
    configuration: TelemetryConfiguration | None = None,
) -> TelemetryService:
    """Creates inactive host telemetry; enabled requests lazily use installed providers.

    No vendor import, device initialization or Pack activation occurs here. Failed
    providers remain unavailable for the active lifetime and can retry after release.
    """
    return TelemetryService(
        machineProvider=None,
        gpuProvider=None,
        machineProviderFactory=PsutilMachineTelemetryProvider,
        gpuProviderFactory=NvmlGpuTelemetryProvider,
        configuration=configuration,
    )
