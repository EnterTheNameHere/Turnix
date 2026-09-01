from __future__ import annotations

import math
from bisect import bisect_left
from datetime import date, datetime, time, timedelta

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_TIME_FORMAT = "%H:%M:%S"
_parsedCache: dict[tuple[str, str], tuple[list[dict[str, object]], list[datetime], datetime]] = {}


def _parseLine(line: str, lineNumber: int) -> dict[str, object]:
    if len(line) < 23 or line[0] != "[" or line[20:22] != "] ":
        raise ValueError(f"Invalid chat record at physical line {lineNumber}.")
    timestampText = line[1:20]
    separator = line.find(" ", 22)
    if separator < 0:
        raise ValueError(f"Chat record has no channel/message separator at physical line {lineNumber}.")
    channel = line[22:separator]
    if not channel:
        raise ValueError(f"Chat record has empty channel at physical line {lineNumber}.")
    try:
        timestamp = datetime.strptime(timestampText, _TIMESTAMP_FORMAT)
    except ValueError as err:
        raise ValueError(f"Invalid chat timestamp at physical line {lineNumber}: {timestampText!r}.") from err
    return {
        "timestamp": timestamp,
        "timestampText": timestampText,
        "channel": channel,
        "message": line[separator + 1 :],
        "rawLine": line,
        "lineNumber": lineNumber,
    }


def _parseTime(value: str, *, fieldName: str) -> time:
    try:
        return datetime.strptime(value, _TIME_FORMAT).time()
    except ValueError as err:
        raise ValueError(f"Application config {fieldName} must be HH:MM:SS, got {value!r}.") from err


def _candidateDates(first: date, last: date) -> set[date]:
    candidates = {first - timedelta(days=1), first, last, last + timedelta(days=1)}
    current = first
    while current <= last:
        candidates.add(current)
        current += timedelta(days=1)
    return candidates


def _distanceToCoverage(candidate: datetime, first: datetime, last: datetime) -> float:
    if candidate < first:
        return (first - candidate).total_seconds()
    if candidate > last:
        return (candidate - last).total_seconds()
    return 0.0


def _inferMediaZero(timestamps: list[datetime], chatStartTime: str) -> datetime:
    if not timestamps:
        raise ValueError("Cannot align an empty chat file.")
    firstTimestamp = timestamps[0]
    lastTimestamp = timestamps[-1]
    clock = _parseTime(chatStartTime, fieldName="chatStartTime")
    candidates = [datetime.combine(candidateDate, clock) for candidateDate in _candidateDates(firstTimestamp.date(), lastTimestamp.date())]
    distances = [(candidate, _distanceToCoverage(candidate, firstTimestamp, lastTimestamp)) for candidate in candidates]
    minimum = min(distance for _, distance in distances)
    best = sorted(candidate for candidate, distance in distances if distance == minimum)
    if len(best) != 1:
        rendered = ", ".join(candidate.strftime(_TIMESTAMP_FORMAT) for candidate in best)
        raise ValueError(
            "chatStartTime is ambiguous across the chat file's date range; "
            f"candidate media-zero timestamps are: {rendered}.",
        )
    return best[0]


def _finiteSeconds(payload: dict[str, object], key: str) -> float:
    value = payload.get(key)
    if type(value) not in {int, float}:
        raise TypeError(f"Chat selector {key!r} must be numeric.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"Chat selector {key!r} must be finite.")
    return result


def _records(ctx, chatPath: str, chatStartTime: str) -> tuple[list[dict[str, object]], list[datetime], datetime]:
    key = (chatPath, chatStartTime)
    cached = _parsedCache.get(key)
    if cached is not None:
        return cached

    parsed = [_parseLine(line, lineNumber) for lineNumber, line in enumerate(ctx.io.readLines(chatPath), start=1)]
    timestamps: list[datetime] = []
    previous: datetime | None = None
    for record in parsed:
        timestamp = record.get("timestamp")
        if not isinstance(timestamp, datetime):
            raise RuntimeError("Parsed chat timestamp has an invalid internal type.")
        if previous is not None and timestamp < previous:
            raise ValueError(
                f"Chat records must be chronological; physical line {record['lineNumber']} moves backward in time.",
            )
        timestamps.append(timestamp)
        previous = timestamp

    mediaZero = _inferMediaZero(timestamps, chatStartTime)
    cached = (parsed, timestamps, mediaZero)
    _parsedCache[key] = cached
    return cached


def _select(ctx, payload):
    if not isinstance(payload, dict):
        raise ValueError("Chat selection requires an object payload.")
    chatPath = ctx.config.get("chatFile")
    chatStartTime = ctx.config.get("chatStartTime")
    if type(chatPath) is not str or type(chatStartTime) is not str:
        raise ValueError("chatFile and chatStartTime must be configured.")

    startVideo = _finiteSeconds(payload, "videoStartSeconds")
    endVideo = _finiteSeconds(payload, "videoEndSeconds")
    if endVideo < startVideo:
        raise ValueError("Chat selector produced an inverted video-time window.")

    parsedRecords, timestamps, mediaZeroWall = _records(ctx, chatPath, chatStartTime)
    startWall = mediaZeroWall + timedelta(seconds=startVideo)
    endWall = mediaZeroWall + timedelta(seconds=endVideo)

    startIndex = bisect_left(timestamps, startWall)
    endIndex = bisect_left(timestamps, endWall)
    records: list[dict[str, object]] = []
    for record in parsedRecords[startIndex:endIndex]:
        retained = dict(record)
        retained.pop("timestamp")
        records.append(retained)

    return {
        "sourcePath": chatPath,
        "chatStartTime": chatStartTime,
        "wallClockAtMediaZero": mediaZeroWall.strftime(_TIMESTAMP_FORMAT),
        "videoStartSeconds": startVideo,
        "videoEndSeconds": endVideo,
        "startWallClock": startWall.strftime(_TIMESTAMP_FORMAT),
        "endWallClock": endWall.strftime(_TIMESTAMP_FORMAT),
        "records": records,
        "text": "\n".join(record["rawLine"] for record in records),
    }


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.chat@1", _select)
