from __future__ import annotations

import math
from bisect import bisect_left
from datetime import date, datetime, time, timedelta

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_TIME_FORMAT = "%H:%M:%S"
_parsedCache: dict[tuple[str, str], tuple[list[dict[str, object]], list[datetime], datetime]] = {}


def _parseLine(line: str, lineNumber: int) -> dict[str, object]:
    """
    Parse one physical chat line into only its source-level structural fields.

    The source grammar is::

        [YYYY-MM-DD HH:mm:SS] #channel message

    The separators after the closing timestamp bracket and after the channel
    token are exactly one ASCII space. Everything after the second separator is
    retained verbatim as message. In particular, this function does not infer a
    username/body split and does not classify user, moderation, information,
    bot, subscription, or other message kinds.

    lineNumber and rawLine are retained as provenance. timestampText/timeText
    are lossless/derived representations of timestamp rather than additional
    interpretation of message contents.
    """
    if type(line) is not str:
        raise TypeError(f"Chat physical line must be exact str at line {lineNumber}.")
    if "\n" in line or "\r" in line:
        raise ValueError(f"Chat record contains an embedded line terminator at physical line {lineNumber}.")
    if len(line) < 23 or line[0] != "[" or line[20:22] != "] ":
        raise ValueError(f"Invalid chat record at physical line {lineNumber}.")

    timestampText = line[1:20]
    try:
        timestamp = datetime.strptime(timestampText, _TIMESTAMP_FORMAT)
    except ValueError as err:
        raise ValueError(f"Invalid chat timestamp at physical line {lineNumber}: {timestampText!r}.") from err

    channelStart = 22
    separator = line.find(" ", channelStart)
    if separator < 0:
        raise ValueError(f"Chat record has no channel/message separator at physical line {lineNumber}.")

    channel = line[channelStart:separator]
    if not channel:
        raise ValueError(f"Chat record has empty channel at physical line {lineNumber}.")
    if not channel.startswith("#"):
        raise ValueError(
            f"Chat channel must begin with '#' at physical line {lineNumber}; got {channel!r}.",
        )
    if any(character.isspace() for character in channel):
        raise ValueError(
            f"Chat channel must not contain whitespace at physical line {lineNumber}; got {channel!r}.",
        )

    return {
        "timestamp": timestamp,
        "timestampText": timestampText,
        "timeText": timestamp.strftime(_TIME_FORMAT),
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


def _offsetSeconds(value: str, *, fieldName: str) -> float:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"Application config {fieldName} must be HH:MM:SS, got {value!r}.")
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError as err:
        raise ValueError(f"Application config {fieldName} must be HH:MM:SS, got {value!r}.") from err
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError(f"Application config {fieldName} must be HH:MM:SS, got {value!r}.")
    return float(hours * 3600 + minutes * 60 + seconds)


def _formatStreamTime(seconds: float) -> str:
    if not math.isfinite(seconds):
        raise ValueError("Stream-relative timestamp must be finite.")
    wholeSeconds = math.floor(seconds)
    sign = "-" if wholeSeconds < 0 else ""
    absolute = abs(wholeSeconds)
    hours, remainder = divmod(absolute, 3600)
    minutes, secondsValue = divmod(remainder, 60)
    return f"{sign}{hours:02d}:{minutes:02d}:{secondsValue:02d}"


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
    candidates = [
        datetime.combine(candidateDate, clock)
        for candidateDate in _candidateDates(firstTimestamp.date(), lastTimestamp.date())
    ]
    distances = [
        (candidate, _distanceToCoverage(candidate, firstTimestamp, lastTimestamp))
        for candidate in candidates
    ]
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

    parsed = [
        _parseLine(line, lineNumber)
        for lineNumber, line in enumerate(ctx.io.readLines(chatPath), start=1)
    ]
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
    """
    Select raw chat records for one half-open video-time interval.

    No message interpretation occurs here. Returned records contain the raw
    source message plus timing/provenance needed by later CodeEntries. Optional
    lookbackSeconds lets a later semantic processor request prior raw evidence
    for stateful interpretation without moving that interpretation into this
    source Pack.
    """
    if not isinstance(payload, dict):
        raise ValueError("Chat selection requires an object payload.")

    chatPath = ctx.config.get("chatFile")
    chatStartTime = ctx.config.get("chatStartTime")
    streamStartTime = ctx.config.get("streamStartTime")
    if type(chatPath) is not str or type(chatStartTime) is not str:
        raise ValueError("chatFile and chatStartTime must be configured.")
    if type(streamStartTime) is not str:
        raise ValueError("Application config streamStartTime must be an HH:MM:SS video offset.")

    startVideo = _finiteSeconds(payload, "videoStartSeconds")
    endVideo = _finiteSeconds(payload, "videoEndSeconds")
    lookbackSeconds = payload.get("lookbackSeconds", 0)
    if type(lookbackSeconds) not in {int, float}:
        raise TypeError("Chat selector 'lookbackSeconds' must be numeric when provided.")
    lookbackSeconds = float(lookbackSeconds)
    if not math.isfinite(lookbackSeconds) or lookbackSeconds < 0:
        raise ValueError("Chat selector 'lookbackSeconds' must be finite and non-negative.")
    if endVideo < startVideo:
        raise ValueError("Chat selector produced an inverted video-time window.")

    parsedRecords, timestamps, mediaZeroWall = _records(ctx, chatPath, chatStartTime)
    streamStartVideoSeconds = _offsetSeconds(streamStartTime, fieldName="streamStartTime")
    streamZeroWall = mediaZeroWall + timedelta(seconds=streamStartVideoSeconds)
    requestedStartWall = mediaZeroWall + timedelta(seconds=startVideo)
    selectionStartWall = requestedStartWall - timedelta(seconds=lookbackSeconds)
    endWall = mediaZeroWall + timedelta(seconds=endVideo)

    startIndex = bisect_left(timestamps, selectionStartWall)
    endIndex = bisect_left(timestamps, endWall)

    records: list[dict[str, object]] = []
    for sourceRecord in parsedRecords[startIndex:endIndex]:
        timestamp = sourceRecord.get("timestamp")
        if not isinstance(timestamp, datetime):
            raise RuntimeError("Parsed chat record has invalid timestamp during selection.")
        streamTimeSeconds = (timestamp - streamZeroWall).total_seconds()
        retained = {
            key: value
            for key, value in sourceRecord.items()
            if key != "timestamp"
        }
        retained["streamTimeSeconds"] = streamTimeSeconds
        retained["streamTime"] = _formatStreamTime(streamTimeSeconds)
        retained["insideRequestedWindow"] = timestamp >= requestedStartWall
        records.append(retained)

    return {
        "sourcePath": chatPath,
        "chatStartTime": chatStartTime,
        "streamStartTime": streamStartTime,
        "streamStartVideoSeconds": streamStartVideoSeconds,
        "wallClockAtMediaZero": mediaZeroWall.strftime(_TIMESTAMP_FORMAT),
        "wallClockAtStreamZero": streamZeroWall.strftime(_TIMESTAMP_FORMAT),
        "videoStartSeconds": startVideo,
        "videoEndSeconds": endVideo,
        "streamStartSeconds": startVideo - streamStartVideoSeconds,
        "streamEndSeconds": endVideo - streamStartVideoSeconds,
        "lookbackSeconds": lookbackSeconds,
        "records": records,
    }


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.chat@1", _select)