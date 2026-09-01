# file: first-party/applications/evilBirthdayAnalysis/packs/chat/codeEntry.py ; version: 2
from __future__ import annotations

import math
from datetime import datetime, timedelta

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


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


def _nonNegativeSeconds(settings: dict[str, object], key: str, default: float) -> float:
    value = settings.get(key, default)
    if type(value) not in {int, float}:
        raise TypeError(f"Profile setting {key!r} must be numeric.")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"Profile setting {key!r} must be a finite non-negative number.")
    return result


def _select(ctx, payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("profile"), dict) or not isinstance(payload.get("transcript"), dict):
        raise ValueError("Chat selection requires profile and transcript window snapshots.")
    chatPath = ctx.config.get("chatFile")
    alignment = ctx.config.get("alignment")
    if type(chatPath) is not str or not isinstance(alignment, dict) or type(alignment.get("wallClockAtMediaZero")) is not str:
        raise ValueError("chatFile and alignment.wallClockAtMediaZero must be configured.")
    try:
        mediaZero = datetime.fromisoformat(alignment["wallClockAtMediaZero"])
    except ValueError as err:
        raise ValueError("alignment.wallClockAtMediaZero must be an ISO date-time.") from err
    if mediaZero.tzinfo is not None:
        raise ValueError(
            "alignment.wallClockAtMediaZero must be timezone-naive because the source chat format contains no timezone.",
        )

    profile = payload["profile"]
    settings = profile.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("Profile settings must be an object.")
    match profile.get("name"):
        case "0-10-30-profile":
            chatBefore = _nonNegativeSeconds(settings, "chatBeforeSeconds", 1800.0)
            chatAfter = _nonNegativeSeconds(settings, "chatAfterSeconds", 1800.0)
        case other:
            raise ValueError(f"Chat Pack does not support profile {other!r}.")

    try:
        transcriptStart = float(payload["transcript"]["startSeconds"])
        transcriptEnd = float(payload["transcript"]["endSeconds"])
    except (KeyError, TypeError, ValueError) as err:
        raise ValueError("Transcript snapshot must contain numeric startSeconds/endSeconds.") from err
    if not math.isfinite(transcriptStart) or not math.isfinite(transcriptEnd) or transcriptEnd < transcriptStart:
        raise ValueError("Transcript snapshot contains an invalid time window.")

    startMedia = transcriptStart - chatBefore
    endMedia = transcriptEnd + chatAfter
    startWall = mediaZero + timedelta(seconds=startMedia)
    endWall = mediaZero + timedelta(seconds=endMedia)
    records: list[dict[str, object]] = []
    for lineNumber, line in enumerate(ctx.io.readLines(chatPath), start=1):
        record = _parseLine(line, lineNumber)
        timestamp = record["timestamp"]
        if not isinstance(timestamp, datetime):
            raise RuntimeError("Parsed chat timestamp has an invalid internal type.")
        if startWall <= timestamp <= endWall:
            retained = dict(record)
            retained.pop("timestamp")
            records.append(retained)
    return {
        "sourcePath": chatPath,
        "wallClockAtMediaZero": alignment["wallClockAtMediaZero"],
        "startWallClock": startWall.strftime(_TIMESTAMP_FORMAT),
        "endWallClock": endWall.strftime(_TIMESTAMP_FORMAT),
        "records": records,
        "text": "\n".join(record["rawLine"] for record in records),
    }


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.chat@1", _select)
