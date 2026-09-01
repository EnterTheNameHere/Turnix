from __future__ import annotations

import math
from datetime import datetime, time, timedelta

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_TIME_FORMAT = "%H:%M:%S"
_parsedCache: dict[tuple[str, str], tuple[list[dict[str, object]], datetime]] = {}


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


def _inferMediaZero(records: list[dict[str, object]], chatStartTime: str) -> datetime:
    if not records:
        raise ValueError("Cannot align an empty chat file.")
    firstTimestamp = records[0].get("timestamp")
    if not isinstance(firstTimestamp, datetime):
        raise RuntimeError("Parsed chat timestamp has an invalid internal type.")
    clock = _parseTime(chatStartTime, fieldName="chatStartTime")
    candidates = [
        datetime.combine(firstTimestamp.date() + timedelta(days=dayOffset), clock)
        for dayOffset in (-1, 0, 1)
    ]
    return min(candidates, key=lambda candidate: abs((candidate - firstTimestamp).total_seconds()))


def _finiteSeconds(payload: dict[str, object], key: str) -> float:
    value = payload.get(key)
    if type(value) not in {int, float}:
        raise TypeError(f"Chat selector {key!r} must be numeric.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"Chat selector {key!r} must be finite.")
    return result


def _records(ctx, chatPath: str, chatStartTime: str) -> tuple[list[dict[str, object]], datetime]:
    key = (chatPath, chatStartTime)
    cached = _parsedCache.get(key)
    if cached is not None:
        return cached
    parsed = [
        _parseLine(line, lineNumber)
        for lineNumber, line in enumerate(ctx.io.readLines(chatPath), start=1)
    ]
    mediaZero = _inferMediaZero(parsed, chatStartTime)
    cached = (parsed, mediaZero)
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

    parsedRecords, mediaZeroWall = _records(ctx, chatPath, chatStartTime)
    startWall = mediaZeroWall + timedelta(seconds=startVideo)
    endWall = mediaZeroWall + timedelta(seconds=endVideo)

    records: list[dict[str, object]] = []
    for record in parsedRecords:
        timestamp = record["timestamp"]
        if not isinstance(timestamp, datetime):
            raise RuntimeError("Parsed chat timestamp has an invalid internal type.")
        if startWall <= timestamp < endWall:
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
