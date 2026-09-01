from __future__ import annotations

import math
import re
from bisect import bisect_left
from datetime import date, datetime, time, timedelta

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_TIME_FORMAT = "%H:%M:%S"
_GIFT_BATCH_MAX_SECONDS = 120
_parsedCache: dict[tuple[str, str], tuple[list[dict[str, object]], list[datetime], datetime]] = {}

_SINGLE_GIFT_RE = re.compile(r"^(?P<sender>.+?) gifted a Tier (?P<tier>[123]) sub to (?P<recipient>.+)!$")
_BULK_GIFT_RE = re.compile(
    r"^(?P<sender>.+?) is gifting (?P<count>\d+) Tier (?P<tier>[123]) Subs to vedal987's community! "
    r"They've gifted a total of (?P<total>\d+) in the channel!$",
)
_TIMEOUT_RE = re.compile(r"^(?P<sender>.+?) has been timed out for (?P<seconds>\d+) seconds$")
_FOSSABOT_LONG_RE = re.compile(r"^@(?P<target>[^,]+), Your message is too long \[warning\]$")
_MULTIPLIER_RE = re.compile(r"^[xX](?P<count>\d+)$")


def _splitUserMessage(value: str, *, lineNumber: int) -> tuple[str, str]:
    separator = value.find(": ")
    if separator <= 0:
        raise ValueError(f"Chat record has no username/message separator at physical line {lineNumber}.")
    username = value[:separator]
    message = value[separator + 2 :]
    if not username:
        raise ValueError(f"Chat record has empty username at physical line {lineNumber}.")
    return username, message


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

    postChannelText = line[separator + 1 :]
    username, message = _splitUserMessage(postChannelText, lineNumber=lineNumber)
    return {
        "timestamp": timestamp,
        "timestampText": timestampText,
        "timeText": timestamp.strftime(_TIME_FORMAT),
        "channel": channel,
        "username": username,
        "message": message,
        "postChannelText": postChannelText,
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


def _vocabulary(ctx) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    path = ctx.config.get("chatEmotesFile", "chatEmotes.json")
    if type(path) is not str:
        raise ValueError("Application config chatEmotesFile must be a string path.")
    definition = ctx.io.readJson(path)
    if not isinstance(definition, dict):
        raise ValueError("Chat emote vocabulary must be an object.")
    emotes = definition.get("emotes")
    composites = definition.get("composites", [])
    if not isinstance(emotes, dict) or not isinstance(composites, list):
        raise ValueError("Chat emote vocabulary requires emotes object and composites list.")

    normalizedEmotes: dict[str, dict[str, object]] = {}
    for name, metadata in emotes.items():
        if type(name) is not str or not name or not isinstance(metadata, dict):
            raise ValueError("Chat emote definitions require non-empty string names and object metadata.")
        normalizedEmotes[name] = dict(metadata)

    normalizedComposites: list[dict[str, object]] = []
    for definition in composites:
        if not isinstance(definition, dict):
            raise ValueError("Chat composite definitions must be objects.")
        tokens = definition.get("tokens")
        if not isinstance(tokens, list) or len(tokens) < 2 or any(type(token) is not str or not token for token in tokens):
            raise ValueError("Chat composite definitions require at least two non-empty string tokens.")
        metadata = {key: value for key, value in definition.items() if key != "tokens"}
        normalizedComposites.append({"tokens": tuple(tokens), "metadata": metadata})
    normalizedComposites.sort(key=lambda item: len(item["tokens"]), reverse=True)
    return normalizedEmotes, normalizedComposites


def _spanIdentity(span: dict[str, object]) -> tuple[object, ...]:
    kind = span.get("kind")
    if kind == "emote":
        return (kind, span.get("name"), span.get("metadata"))
    if kind == "composite":
        return (kind, tuple(span.get("tokens", [])), span.get("metadata"))
    if kind == "command":
        return (kind, span.get("command"), span.get("arguments"))
    if kind == "text":
        return (kind, span.get("text"))
    return (kind, repr(span))


def _appendSpan(spans: list[dict[str, object]], span: dict[str, object]) -> None:
    if spans and span.get("kind") in {"emote", "composite"} and _spanIdentity(spans[-1]) == _spanIdentity(span):
        spans[-1]["count"] = int(spans[-1].get("count", 1)) + int(span.get("count", 1))
        return
    if spans and span.get("kind") == "text" and spans[-1].get("kind") == "text":
        spans[-1]["text"] = f"{spans[-1]['text']} {span['text']}"
        return
    spans.append(span)


def _matchComposite(tokens: list[str], index: int, composites: list[dict[str, object]]) -> dict[str, object] | None:
    for composite in composites:
        pattern = composite["tokens"]
        if tuple(tokens[index : index + len(pattern)]) == pattern:
            return composite
    return None


def _collapseWholeSequence(spans: list[dict[str, object]]) -> list[dict[str, object]]:
    count = len(spans)
    if count < 2:
        return spans
    for unitLength in range(1, count // 2 + 1):
        if count % unitLength:
            continue
        repetitions = count // unitLength
        unit = spans[:unitLength]
        if repetitions > 1 and all(spans[offset : offset + unitLength] == unit for offset in range(0, count, unitLength)):
            return [{"kind": "repeat", "count": repetitions, "spans": unit}]
    return spans


def _lexMessage(message: str, emotes: dict[str, dict[str, object]], composites: list[dict[str, object]]) -> list[dict[str, object]]:
    tokens = message.split()
    spans: list[dict[str, object]] = []
    index = 0
    while index < len(tokens):
        composite = _matchComposite(tokens, index, composites)
        if composite is not None:
            pattern = composite["tokens"]
            _appendSpan(
                spans,
                {
                    "kind": "composite",
                    "tokens": list(pattern),
                    "count": 1,
                    "metadata": dict(composite["metadata"]),
                },
            )
            index += len(pattern)
            continue

        token = tokens[index]
        multiplier = _MULTIPLIER_RE.fullmatch(token)
        if multiplier is not None and spans and spans[-1].get("kind") in {"emote", "composite"}:
            value = int(multiplier.group("count"))
            if value > 0:
                spans[-1]["count"] = int(spans[-1].get("count", 1)) * value
                index += 1
                continue

        metadata = emotes.get(token)
        if metadata is not None:
            _appendSpan(spans, {"kind": "emote", "name": token, "count": 1, "metadata": dict(metadata)})
            index += 1
            continue

        if token.startswith("!") and len(token) > 1 and not spans:
            _appendSpan(spans, {"kind": "command", "command": token[1:], "arguments": []})
            index += 1
            continue

        if spans and spans[-1].get("kind") == "command":
            arguments = spans[-1]["arguments"]
            if isinstance(arguments, list):
                arguments.append(token)
                index += 1
                continue

        _appendSpan(spans, {"kind": "text", "text": token})
        index += 1

    return _collapseWholeSequence(spans)


def _renderSpan(span: dict[str, object]) -> str:
    kind = span.get("kind")
    if kind == "text":
        return str(span.get("text", ""))
    if kind == "emote":
        text = str(span.get("name", ""))
        count = int(span.get("count", 1))
        return text if count == 1 else f"{text} x{count}"
    if kind == "composite":
        text = " ".join(str(token) for token in span.get("tokens", []))
        count = int(span.get("count", 1))
        return text if count == 1 else f"{text} x{count}"
    if kind == "command":
        command = f"!{span.get('command', '')}"
        arguments = span.get("arguments", [])
        if isinstance(arguments, list) and arguments:
            return command + " " + " ".join(str(argument) for argument in arguments)
        return command
    if kind == "repeat":
        nested = span.get("spans", [])
        if not isinstance(nested, list):
            return ""
        text = " ".join(part for part in (_renderSpan(item) for item in nested) if part)
        return f"({text}) x{int(span.get('count', 1))}"
    return ""


def _renderSpans(spans: list[dict[str, object]]) -> str:
    return " ".join(part for part in (_renderSpan(span) for span in spans) if part)


def _knownBotEvent(username: str, message: str) -> dict[str, object] | None:
    if username.casefold() != "fossabot":
        return None
    warning = _FOSSABOT_LONG_RE.fullmatch(message)
    if warning is not None:
        return {"type": "botWarning", "bot": username, "warning": "messageTooLong", "target": warning.group("target")}
    if message.startswith("Neuro-sama Headquarters: "):
        return {"type": "botInfo", "bot": username, "topic": "neuroHeadquarters", "message": message}
    if message.startswith("Wishlist Abandoned Archive on Steam: "):
        return {"type": "botInfo", "bot": username, "topic": "abandonedArchiveWishlist", "message": message}
    return None


def _generatedEvent(username: str, message: str) -> dict[str, object] | None:
    bulk = _BULK_GIFT_RE.fullmatch(message)
    if bulk is not None and bulk.group("sender").casefold() == username.casefold():
        return {
            "type": "subscriptionGiftBatch",
            "sender": bulk.group("sender"),
            "tier": int(bulk.group("tier")),
            "count": int(bulk.group("count")),
            "totalGifted": int(bulk.group("total")),
            "recipients": [],
        }

    single = _SINGLE_GIFT_RE.fullmatch(message)
    if single is not None and single.group("sender").casefold() == username.casefold():
        return {
            "type": "subscriptionGift",
            "sender": single.group("sender"),
            "tier": int(single.group("tier")),
            "recipient": single.group("recipient"),
        }

    timeout = _TIMEOUT_RE.fullmatch(message)
    if timeout is not None and timeout.group("sender").casefold() == username.casefold():
        return {
            "type": "timeout",
            "username": timeout.group("sender"),
            "seconds": int(timeout.group("seconds")),
        }
    return None


def _eventText(event: dict[str, object]) -> str:
    eventType = event.get("type")
    if eventType == "subscriptionGiftBatch":
        return f"[gift {event['count']}xT{event['tier']}; total {event['totalGifted']}]"
    if eventType == "subscriptionGift":
        return f"[gift T{event['tier']} to {event['recipient']}]"
    if eventType == "timeout":
        return f"[timeout {event['seconds']}s]"
    return ""


def _analyzeRecords(records: list[dict[str, object]], emotes: dict[str, dict[str, object]], composites: list[dict[str, object]]) -> list[str]:
    rendered: list[str] = []
    openBatches: dict[tuple[str, int], tuple[datetime, dict[str, object], int]] = {}

    for record in records:
        username = record.get("username")
        message = record.get("message")
        timestamp = record.get("timestamp")
        timeText = record.get("timeText")
        if type(username) is not str or type(message) is not str or not isinstance(timestamp, datetime) or type(timeText) is not str:
            raise RuntimeError("Parsed chat record has invalid internal analysis fields.")

        generated = _generatedEvent(username, message)
        if generated is not None:
            eventType = generated["type"]
            record["analysis"] = {"kind": "generatedEvent", "event": generated, "includedInText": True}
            if eventType == "subscriptionGiftBatch":
                key = (username.casefold(), int(generated["tier"]))
                openBatches[key] = (timestamp, generated, int(record["lineNumber"]))
                rendered.append(f"{timeText} {username}: {_eventText(generated)}")
                continue
            if eventType == "subscriptionGift":
                key = (username.casefold(), int(generated["tier"]))
                batch = openBatches.get(key)
                if batch is not None:
                    openedAt, batchEvent, batchLine = batch
                    age = (timestamp - openedAt).total_seconds()
                    recipients = batchEvent.get("recipients")
                    expected = int(batchEvent.get("count", 0))
                    if 0 <= age <= _GIFT_BATCH_MAX_SECONDS and isinstance(recipients, list) and len(recipients) < expected:
                        recipients.append(generated["recipient"])
                        record["analysis"] = {
                            "kind": "generatedEvent",
                            "event": generated,
                            "includedInText": False,
                            "partOfGiftBatchLineNumber": batchLine,
                        }
                        if len(recipients) >= expected:
                            openBatches.pop(key, None)
                        continue
                    openBatches.pop(key, None)
                rendered.append(f"{timeText} {username}: {_eventText(generated)}")
                continue
            rendered.append(f"{timeText} {username}: {_eventText(generated)}")
            continue

        botEvent = _knownBotEvent(username, message)
        if botEvent is not None:
            record["analysis"] = {"kind": "botEvent", "event": botEvent, "includedInText": False}
            continue

        spans = _lexMessage(message, emotes, composites)
        compactMessage = _renderSpans(spans)
        record["analysis"] = {"kind": "userMessage", "spans": spans, "includedInText": True}
        rendered.append(f"{timeText} {username}: {compactMessage}")

    return rendered


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
    emotes, composites = _vocabulary(ctx)
    startWall = mediaZeroWall + timedelta(seconds=startVideo)
    endWall = mediaZeroWall + timedelta(seconds=endVideo)

    startIndex = bisect_left(timestamps, startWall)
    endIndex = bisect_left(timestamps, endWall)
    analysisRecords: list[dict[str, object]] = []
    for record in parsedRecords[startIndex:endIndex]:
        analysisRecords.append(dict(record))

    rendered = _analyzeRecords(analysisRecords, emotes, composites)
    records: list[dict[str, object]] = []
    for record in analysisRecords:
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
        "text": "\n".join(rendered),
    }


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.chat@1", _select)
