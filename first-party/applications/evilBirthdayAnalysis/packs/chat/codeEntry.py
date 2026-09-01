# file: first-party/applications/evilBirthdayAnalysis/packs/chat/codeEntry.py ; version: 1
from __future__ import annotations

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


def _select(ctx, payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("profile"), dict) or not isinstance(payload.get("transcript"), dict):
        raise ValueError("Chat selection requires profile and transcript window snapshots.")
    chatPath = ctx.config.get("chatFile")
    alignment = ctx.config.get("alignment")
    if type(chatPath) is not str or not isinstance(alignment, dict) or type(alignment.get("wallClockAtMediaZero")) is not str:
        raise ValueError("chatFile and alignment.wallClockAtMediaZero must be configured.")
    mediaZero = datetime.fromisoformat(alignment["wallClockAtMediaZero"])
    profile = payload["profile"]
    settings = profile.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("Profile settings must be an object.")
    match profile.get("name"):
        case "0-10-30-profile":
            chatBefore = float(settings.get("chatBeforeSeconds", 1800))
            chatAfter = float(settings.get("chatAfterSeconds", 1800))
        case other:
            raise ValueError(f"Chat Pack does not support profile {other!r}.")
    startMedia = float(payload["transcript"]["startSeconds"]) - chatBefore
    endMedia = float(payload["transcript"]["endSeconds"]) + chatAfter
    startWall = mediaZero + timedelta(seconds=startMedia)
    endWall = mediaZero + timedelta(seconds=endMedia)
    records: list[dict[str, object]] = []
    for lineNumber, line in enumerate(ctx.io.readLines(chatPath), start=1):
        record = _parseLine(line, lineNumber)
        if startWall <= record["timestamp"] <= endWall:
            record = dict(record)
            record.pop("timestamp")
            records.append(record)
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
