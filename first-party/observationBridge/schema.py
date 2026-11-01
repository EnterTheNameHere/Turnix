# file: first-party/observationBridge/schema.py ; version: 1
"""Allowlisted, transport-independent observation JSON; no generic object serialization."""
from __future__ import annotations

import json
import math

from backend.llm.streamObservation import StreamObservationSnapshot, StreamObservationWindow, StreamOutcome
from backend.observation import ObservationPublicationSnapshot, ObservationRequest, ObservationLifetime
from backend.telemetry.model import (
    CpuStaticObservation, CpuUtilizationObservation, GpuObservation,
    SwapObservation, SystemMemoryObservation,
)

SCHEMA_ID = "actant.observation-publication@1"
MAX_SAMPLES = 512
MAX_WINDOWS = 512
MAX_CORES = 256
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def integer(value: object) -> str | None:
    """Preserves exact integers as decimal strings; unknown values stay null."""
    return str(value) if type(value) is int else None


def number(value: object) -> int | float | None:
    """Allows finite native numerical evidence only, never custom conversions or NaN."""
    if type(value) not in (int, float):
        return None
    try:
        return value if math.isfinite(value) and abs(value) <= 2**53 - 1 else None
    except OverflowError:
        return None


def signal(value: object, unit: str, *, integral: bool = False) -> dict:
    """Tags explicit availability/units without publishing provider strings or error messages."""
    scalar = integer(value) if integral else number(value)
    return {"availability": "unavailable" if scalar is None else "available", "unit": unit, "value": scalar}


def _hardware(sample) -> dict:
    """Selects graphable host measurements while excluding labels, metadata and diagnostics."""
    memory, swap, cpu, topology, gpu = sample.systemMemory, sample.swap, sample.cpuUtilization, sample.cpuStatic, sample.gpu
    memoryFields = {}
    for name in ("totalBytes", "availableBytes", "usedBytes", "freeBytes", "usagePercent"):
        value = getattr(memory, name) if type(memory) is SystemMemoryObservation else None
        memoryFields[name] = signal(value, "percent" if name == "usagePercent" else "bytes", integral=name != "usagePercent")
    swapFields = {}
    for name in ("totalBytes", "usedBytes", "freeBytes", "usagePercent"):
        value = getattr(swap, name) if type(swap) is SwapObservation else None
        swapFields[name] = signal(value, "percent" if name == "usagePercent" else "bytes", integral=name != "usagePercent")
    cores = cpu.perLogicalCorePercent if type(cpu) is CpuUtilizationObservation and type(cpu.perLogicalCorePercent) is tuple else ()
    gpuFields = {}
    for name, unit, integral in (
        ("deviceIndex", "index", True), ("totalVramBytes", "bytes", True),
        ("usedVramBytes", "bytes", True), ("freeVramBytes", "bytes", True),
        ("gpuUtilizationPercent", "percent", False), ("memoryControllerUtilizationPercent", "percent", False),
        ("temperatureCelsius", "celsius", False), ("powerDrawWatts", "watts", False),
        ("powerLimitWatts", "watts", False), ("graphicsClockMhz", "megahertz", True),
        ("memoryClockMhz", "megahertz", True), ("fanSpeedPercent", "percent", False),
    ):
        gpuFields[name] = signal(getattr(gpu, name) if type(gpu) is GpuObservation else None, unit, integral=integral)
    return {
        "collectionStartNs": integer(sample.collectionStartNs), "collectionEndNs": integer(sample.collectionEndNs),
        "displayWallTimeNs": integer(sample.sampledTimeNs),
        "targetIntervalSeconds": number(sample.sampleIntervalSeconds), "actualIntervalSeconds": number(sample.actualIntervalSeconds),
        "memory": memoryFields, "swap": swapFields,
        "cpu": {
            "aggregatePercent": signal(cpu.aggregatePercent if type(cpu) is CpuUtilizationObservation else None, "percent"),
            "perLogicalCorePercent": [signal(value, "percent") for value in cores[:MAX_CORES]],
            "perCoreUnavailable": type(cpu) is not CpuUtilizationObservation,
            "droppedCoreCount": integer(max(0, len(cores) - MAX_CORES)),
            "physicalCores": signal(topology.physicalCores if type(topology) is CpuStaticObservation else None, "cores", integral=True),
            "logicalCores": signal(topology.logicalCores if type(topology) is CpuStaticObservation else None, "cores", integral=True),
        }, "gpu": gpuFields,
    }


def _window(window: StreamObservationWindow) -> dict:
    """Keeps delivery and gap evidence in its original half-open monotonic interval."""
    return {
        **{name: integer(getattr(window, name)) for name in (
            "startNs", "endNs", "characterCount", "deliveryCount", "firstTextNs", "lastTextNs",
            "gapCount", "gapSumNs", "minGapNs", "maxGapNs",
        )},
        "partial": window.partial is True,
        "charactersPerSecond": signal(window.charactersPerSecond, "unicode-code-points/second"),
        # Exact gapSumNs/gapCount above allow lossless derivation of mean spacing.
    }


def serialize(snapshot: ObservationPublicationSnapshot, publicationId: str, revision: int) -> bytes:
    """Encodes one bounded selected snapshot, never Python internals or engine result data.

    All integer evidence, including counts/bytes/revisions, uses decimal strings.
    Fixed markers replace raw error/provider strings. Window overlap references
    identify collection activity only; uncovered spans do not prove missing polls.
    """
    if type(snapshot) is not ObservationPublicationSnapshot or type(snapshot.request) is not ObservationRequest:
        raise TypeError("Expected mediated publication evidence.")
    request, observation = snapshot.request, snapshot.observation
    if request.historySamples > MAX_SAMPLES or request.historyWindows > MAX_WINDOWS:
        raise ValueError("Source retention exceeds bridge limits.")
    if len(observation.hardware) > MAX_SAMPLES or len(observation.windows) > MAX_WINDOWS:
        raise ValueError("Snapshot exceeds bridge limits.")
    stream = observation.stream
    streamData = None
    if type(stream) is StreamObservationSnapshot:
        if len(stream.windows) > MAX_WINDOWS:
            raise ValueError("Stream retention exceeds bridge limits.")
        streamData = {
            **{name: integer(getattr(stream, name)) for name in (
                "originNs", "observedThroughNs", "endNs", "firstTextNs", "lastTextNs",
                "characterCount", "deliveryCount", "droppedWindowCount", "firstTextLatencyNs", "silenceNs",
            )},
            "availability": "available" if stream.unavailableReason is None else "unavailable",
            "state": "unavailable" if stream.unavailableReason is not None else "not-started" if stream.originNs is None else "active" if stream.active else "terminal",
            "outcome": stream.outcome.value if type(stream.outcome) is StreamOutcome else None,
            "active": stream.active is True, "released": stream.released is True,
            "windows": [_window(window) for window in stream.windows],
        }
    hardwareIndex = {id(sample): index for index, sample in enumerate(observation.hardware)}
    # PM-03 correlation references the same immutable hardware objects from this snapshot.
    correlations = [{
        "startNs": integer(row.stream.startNs), "endNs": integer(row.stream.endNs),
        "hardwareIndices": [hardwareIndex[id(sample)] for sample in row.hardware if id(sample) in hardwareIndex],
        "uncoveredIntervalsNs": [[integer(start), integer(end)] for start, end in row.uncoveredIntervals],
    } for row in observation.windows]
    data = {
        "schemaId": SCHEMA_ID, "publicationId": publicationId, "sessionId": snapshot.sessionId,
        "revision": integer(revision),
        "encodings": {"integerEvidence": "decimal-string", "ratesAndPercent": "finite-json-number"},
        "units": {"timestampsAndGaps": "nanoseconds", "streamCharacters": "unicode-code-points", "deliveries": "events", "displayWallTimeNs": "unix-epoch-nanoseconds"},
        "clock": {"kind": "host-process-monotonic", "epochId": snapshot.clockEpochId, "nanosecondsEncoding": "decimal-string"},
        "scope": "whole-host-hardware/application-selected-stream",
        "sourceLifetime": snapshot.lifetime.value if type(snapshot.lifetime) is ObservationLifetime else None,
        "needs": {name: getattr(request, name) for name in ("memory", "cpu", "gpu", "stream")},
        "retention": {"hardwareSamples": integer(request.historySamples), "streamWindows": integer(request.historyWindows),
                      "hardwareMayBeTruncated": observation.hardwareHistoryMayBeTruncated is True,
                      "hardwareDroppedCount": None},
        "sampleIntervalSeconds": number(request.sampleIntervalSeconds), "windowSeconds": number(request.windowSeconds),
        "hardwareAvailability": "unavailable" if observation.hardwareUnavailableReason is not None else "as-reported-per-signal",
        "streamAvailability": "unavailable" if observation.streamUnavailableReason is not None else "disabled" if not request.stream else "as-reported",
        "collectionStopped": observation.collectionStopped is True,
        "hardware": [_hardware(sample) for sample in observation.hardware],
        "stream": streamData, "correlation": correlations,
        "correlationMeaning": "collection-interval-overlap; sequential-reads; no-interpolation",
    }
    encoded = json.dumps(data, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("ascii")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise ValueError("Publication exceeds response byte limit.")
    return encoded
