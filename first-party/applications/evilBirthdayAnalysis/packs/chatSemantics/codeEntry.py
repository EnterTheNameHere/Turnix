# file: first-party/applications/evilBirthdayAnalysis/packs/chatSemantics/codeEntry.py ; version: 1
from __future__ import annotations

import re
from collections.abc import Mapping

_SEMANTIC_KEYS = ("semanticClass", "entity", "target")
_TRUSTED_CLASSIFICATION_SOURCE = "userDefined"
_GIFT_BATCH_MAX_SECONDS = 120
_LINE_SEMANTIC_PROCESSOR_REVISION = 1

_SINGLE_GIFT_RE = re.compile(r"^(?P<sender>.+?) gifted a Tier (?P<tier>[123]) sub to (?P<recipient>.+)!$")
_BULK_GIFT_RE = re.compile(
    r"^(?P<sender>.+?) is gifting (?P<count>\d+) Tier (?P<tier>[123]) Subs to vedal987's community! "
    r"They've gifted a total of (?P<total>\d+) in the channel!$",
)
_TIMEOUT_RE = re.compile(r"^(?P<sender>.+?) has been timed out for (?P<seconds>\d+) seconds$")
_FOSSABOT_LONG_RE = re.compile(r"^@(?P<target>[^,]+), Your message is too long \[warning\]$")
_MULTIPLIER_RE = re.compile(r"^[xX](?P<count>\d+)$")


def _meaning(metadata: Mapping[str, object]) -> dict[str, object] | None:
    if metadata.get("classificationSource") != _TRUSTED_CLASSIFICATION_SOURCE:
        return None
    meaning = {key: metadata[key] for key in _SEMANTIC_KEYS if key in metadata}
    if not meaning:
        return None
    meaning["classificationSource"] = _TRUSTED_CLASSIFICATION_SOURCE
    return meaning


def _evaluateSpan(span: dict[str, object], *, multiplier: int = 1) -> tuple[bool, bool, list[dict[str, object]]]:
    kind = span.get("kind")

    if kind in {"emote", "composite"}:
        metadata = span.get("metadata")
        if not isinstance(metadata, Mapping):
            raise TypeError(f"Chat semantic span {kind!r} requires metadata.")
        count = span.get("count", 1)
        if type(count) is not int or count <= 0:
            raise ValueError(f"Chat semantic span {kind!r} requires a positive exact integer count.")
        meaning = _meaning(metadata)
        if meaning is None:
            return True, False, []
        return True, True, [{"meaning": meaning, "count": count * multiplier}]

    if kind == "repeat":
        count = span.get("count")
        nested = span.get("spans")
        if type(count) is not int or count <= 1:
            raise ValueError("Chat semantic repeat spans require an exact integer count greater than one.")
        if not isinstance(nested, list) or not nested:
            raise ValueError("Chat semantic repeat spans require a non-empty spans list.")
        lexical = True
        semantic = True
        units: list[dict[str, object]] = []
        for nestedSpan in nested:
            if not isinstance(nestedSpan, dict):
                raise TypeError("Chat semantic repeat spans must contain objects.")
            nestedLexical, nestedSemantic, nestedUnits = _evaluateSpan(
                nestedSpan,
                multiplier=multiplier * count,
            )
            lexical = lexical and nestedLexical
            semantic = semantic and nestedSemantic
            units.extend(nestedUnits)
        return lexical, semantic, units

    if kind in {"text", "command"}:
        return False, False, []

    raise ValueError(f"Unsupported chat semantic span kind: {kind!r}.")


def _evaluate(_ctx, payload):
    if not isinstance(payload, dict):
        raise ValueError("Chat semantic evaluation requires an object payload.")
    spans = payload.get("spans")
    if not isinstance(spans, list):
        raise TypeError("Chat semantic evaluation spans must be a list.")
    if not spans:
        return {
            "lexicallyComplete": False,
            "semanticallyComplete": False,
            "aggregationEligible": False,
            "semanticUnits": [],
            "structurallyCompressed": False,
        }

    lexical = True
    semantic = True
    units: list[dict[str, object]] = []
    structurallyCompressed = False
    for span in spans:
        if not isinstance(span, dict):
            raise TypeError("Chat semantic evaluation spans must contain objects.")
        if span.get("kind") == "repeat":
            structurallyCompressed = True
        spanLexical, spanSemantic, spanUnits = _evaluateSpan(span)
        lexical = lexical and spanLexical
        semantic = semantic and spanSemantic
        units.extend(spanUnits)

    return {
        "lexicallyComplete": lexical,
        "semanticallyComplete": semantic,
        "aggregationEligible": semantic,
        "semanticUnits": units if semantic else [],
        "structurallyCompressed": structurallyCompressed,
    }


def _vocabulary(
    ctx,
) -> tuple[
    dict[str, dict[str, object]],
    list[dict[str, object]],
    dict[str, object],
]:
    """Loads one coherent vocabulary plus strong Actant source provenance."""
    path = ctx.config.get("chatEmotesFile", "chatEmotes.json")
    if type(path) is not str:
        raise ValueError("Application config chatEmotesFile must be a string path.")

    for _attempt in range(3):
        before = ctx.io.observeFile(path, contentHash=True)
        definition = ctx.io.readJson(path)
        after = ctx.io.observeFile(path, contentHash=True)
        if before == after:
            break
    else:
        raise RuntimeError(f"Chat emote vocabulary changed repeatedly while being read: {path!r}.")

    if after.get("state") != "file":
        raise RuntimeError(f"Chat emote vocabulary source is not a regular file: {path!r}.")
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
    seenPatterns: set[tuple[str, ...]] = set()
    for compositeDefinition in composites:
        if not isinstance(compositeDefinition, dict):
            raise ValueError("Chat composite definitions must be objects.")
        tokens = compositeDefinition.get("tokens")
        if not isinstance(tokens, list) or len(tokens) < 2 or any(type(token) is not str or not token for token in tokens):
            raise ValueError("Chat composite definitions require at least two non-empty string tokens.")
        pattern = tuple(tokens)
        unknown = [token for token in pattern if token not in normalizedEmotes]
        if unknown:
            raise ValueError(f"Chat composite references unknown emote token(s): {', '.join(unknown)}.")
        if pattern in seenPatterns:
            raise ValueError(f"Duplicate chat composite pattern: {' '.join(pattern)}.")
        seenPatterns.add(pattern)
        metadata = {key: value for key, value in compositeDefinition.items() if key != "tokens"}
        normalizedComposites.append({"tokens": pattern, "metadata": metadata})

    normalizedComposites.sort(key=lambda item: len(item["tokens"]), reverse=True)
    return normalizedEmotes, normalizedComposites, after


def _splitUserMessage(value: str) -> tuple[str, str] | None:
    """Dynamically recognize the current username/body form without requiring it at ingestion."""
    separator = value.find(": ")
    if separator <= 0:
        return None
    username = value[:separator]
    if not username:
        return None
    return username, value[separator + 2 :]


def _spanIdentity(span: dict[str, object]) -> tuple[object, ...]:
    kind = span.get("kind")
    if kind == "emote":
        return (kind, span.get("name"), repr(span.get("metadata")))
    if kind == "composite":
        return (kind, tuple(span.get("tokens", [])), repr(span.get("metadata")))
    if kind == "command":
        return (kind, span.get("command"), tuple(span.get("arguments", [])))
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
        if repetitions > 1 and all(
            spans[offset : offset + unitLength] == unit
            for offset in range(0, count, unitLength)
        ):
            return [{"kind": "repeat", "count": repetitions, "spans": unit}]
    return spans


def _collapseRepeatedTextSequence(message: str, spans: list[dict[str, object]]) -> list[dict[str, object]]:
    """Collapses an exact whole-message repeated text token sequence."""
    if len(spans) != 1 or spans[0].get("kind") != "text":
        return spans

    tokens = message.split()
    count = len(tokens)
    if count < 2:
        return spans
    for unitLength in range(1, count // 2 + 1):
        if count % unitLength:
            continue
        repetitions = count // unitLength
        unit = tokens[:unitLength]
        if repetitions > 1 and all(
            tokens[offset : offset + unitLength] == unit
            for offset in range(0, count, unitLength)
        ):
            return [
                {
                    "kind": "repeat",
                    "count": repetitions,
                    "spans": [{"kind": "text", "text": " ".join(unit)}],
                }
            ]
    return spans


def _occurrenceCount(tokens: list[str], nextIndex: int) -> tuple[int, int]:
    if nextIndex >= len(tokens):
        return 1, nextIndex
    multiplier = _MULTIPLIER_RE.fullmatch(tokens[nextIndex])
    if multiplier is None:
        return 1, nextIndex
    count = int(multiplier.group("count"))
    if count <= 0:
        return 1, nextIndex
    return count, nextIndex + 1


def _lexMessage(message: str, emotes: dict[str, dict[str, object]], composites: list[dict[str, object]]) -> list[dict[str, object]]:
    tokens = message.split()
    spans: list[dict[str, object]] = []
    index = 0
    while index < len(tokens):
        composite = _matchComposite(tokens, index, composites)
        if composite is not None:
            pattern = composite["tokens"]
            count, nextIndex = _occurrenceCount(tokens, index + len(pattern))
            _appendSpan(
                spans,
                {
                    "kind": "composite",
                    "tokens": list(pattern),
                    "count": count,
                    "metadata": dict(composite["metadata"]),
                },
            )
            index = nextIndex
            continue

        token = tokens[index]
        metadata = emotes.get(token)
        if metadata is not None:
            count, nextIndex = _occurrenceCount(tokens, index + 1)
            _appendSpan(
                spans,
                {"kind": "emote", "name": token, "count": count, "metadata": dict(metadata)},
            )
            index = nextIndex
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

    spans = _collapseWholeSequence(spans)
    return _collapseRepeatedTextSequence(message, spans)


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


def _semanticCellAddress(lineNumber: int) -> str:
    """Returns the stable logical address of one line-local semantic product."""
    return f"evilanalysis/chat/line/{lineNumber}/semantic"


def _lineSemantic(
    rawMessage: str,
    *,
    emotes: dict[str, dict[str, object]],
    composites: list[dict[str, object]],
) -> dict[str, object]:
    """Derives only semantics that are a function of this physical line.

    Cross-line reconstruction, currently gift-batch membership/recipients,
    intentionally remains outside this product. The persisted value can
    therefore be reused independently by any later consumer without silently
    embedding one window's neighboring context.
    """
    split = _splitUserMessage(rawMessage)
    if split is None:
        return {
            "kind": "unknownMessage",
            "rawMessage": rawMessage,
        }

    username, message = split
    generated = _generatedEvent(username, message)
    if generated is not None:
        return {
            "kind": "generatedEvent",
            "username": username,
            "body": message,
            "event": generated,
        }

    botEvent = _knownBotEvent(username, message)
    if botEvent is not None:
        return {
            "kind": "botEvent",
            "username": username,
            "body": message,
            "event": botEvent,
        }

    spans = _lexMessage(message, emotes, composites)
    return {
        "kind": "userMessage",
        "username": username,
        "body": message,
        "spans": spans,
    }


def _lineSemanticBasis(
    rawRecord: dict[str, object],
    *,
    vocabularyObservation: dict[str, object],
) -> dict[str, object]:
    """Returns requirements that determine line-local semantic authority."""
    return {
        "rawLine": rawRecord.get("rawLine"),
        "processorRevision": _LINE_SEMANTIC_PROCESSOR_REVISION,
        "vocabularyObservation": vocabularyObservation,
    }


def _persistentLineSemantic(
    ctx,
    rawRecord: dict[str, object],
    *,
    emotes: dict[str, dict[str, object]],
    composites: list[dict[str, object]],
    vocabularyObservation: dict[str, object],
    sourcePath: str,
    sourceObservation: dict[str, object],
) -> dict[str, object]:
    """Returns current line semantics, reusing or replacing Actant memory.

    The logical address remains stable when source or processing changes.
    Memory-managed revisioning records successive authoritative states. The
    Pack owns the domain validity rule: exact raw input, semantic processor
    revision, and vocabulary source must still match.

    File-level source observation is retained as provenance but deliberately
    does not invalidate an unchanged raw line merely because another part of
    the file changed. This is the implementation-level form of the persistent
    derived-state model and should later be promoted into the design docs.
    """
    lineNumber = rawRecord.get("lineNumber")
    rawMessage = rawRecord.get("message")
    rawLine = rawRecord.get("rawLine")
    if type(lineNumber) is not int or lineNumber <= 0:
        raise TypeError("Raw chat line semantic processing requires positive lineNumber.")
    if type(rawMessage) is not str or type(rawLine) is not str:
        raise TypeError("Raw chat line semantic processing requires message and rawLine strings.")

    address = _semanticCellAddress(lineNumber)
    basis = _lineSemanticBasis(
        rawRecord,
        vocabularyObservation=vocabularyObservation,
    )
    stored = ctx.memory.load(address)
    if isinstance(stored, dict) and stored.get("basis") == basis:
        semantic = stored.get("semantic")
        if isinstance(semantic, dict):
            return semantic

    semantic = _lineSemantic(
        rawMessage,
        emotes=emotes,
        composites=composites,
    )
    transaction = ctx.memory.openTransaction()
    transaction.set(
        address,
        {
            "basis": basis,
            "source": {
                "path": sourcePath,
                "observation": sourceObservation,
                "lineNumber": lineNumber,
            },
            "semantic": semantic,
        },
    )
    transaction.commit()
    return semantic


def _interpret(ctx, payload):
    """Interpret raw chat records using persistent line-local semantic products.

    Line-local classification/lexing is reusable across windows and
    ApplicationRuns. Multi-line gift reconstruction is performed after those
    products are loaded so one persisted semantic cell never depends on the
    accidental boundaries or lookback of a particular analysis request.
    """
    if not isinstance(payload, dict):
        raise ValueError("Chat interpretation requires an object payload.")
    rawRecords = payload.get("records")
    sourcePath = payload.get("sourcePath")
    sourceObservation = payload.get("sourceObservation")
    if not isinstance(rawRecords, list):
        raise TypeError("Chat interpretation requires a records list.")
    if type(sourcePath) is not str or not sourcePath:
        raise TypeError("Chat interpretation requires sourcePath.")
    if not isinstance(sourceObservation, dict):
        raise TypeError("Chat interpretation requires sourceObservation.")

    emotes, composites, vocabularyObservation = _vocabulary(ctx)
    records: list[dict[str, object]] = []
    rendered: list[str] = []
    openBatches: dict[tuple[str, int], tuple[float, dict[str, object], int]] = {}

    for rawRecord in rawRecords:
        if not isinstance(rawRecord, dict):
            raise TypeError("Chat interpretation records must contain objects.")
        record = dict(rawRecord)
        rawMessage = record.get("message")
        streamTimeSeconds = record.get("streamTimeSeconds")
        streamTime = record.get("streamTime")
        lineNumber = record.get("lineNumber")
        insideRequestedWindow = record.get("insideRequestedWindow", True)
        if (
            type(rawMessage) is not str
            or type(streamTimeSeconds) not in {int, float}
            or type(streamTime) is not str
            or type(lineNumber) is not int
            or type(insideRequestedWindow) is not bool
        ):
            raise TypeError("Raw chat record is missing required source/timing evidence.")

        semantic = _persistentLineSemantic(
            ctx,
            record,
            emotes=emotes,
            composites=composites,
            vocabularyObservation=vocabularyObservation,
            sourcePath=sourcePath,
            sourceObservation=sourceObservation,
        )
        kind = semantic.get("kind")

        if kind == "unknownMessage":
            analysis = {
                "kind": "unknownMessage",
                "includedInText": insideRequestedWindow,
                "streamTimeSeconds": float(streamTimeSeconds),
                "streamTime": streamTime,
                "rawMessage": rawMessage,
            }
            record["analysis"] = analysis
            if insideRequestedWindow:
                rendered.append(f"{streamTime} [unclassified] {rawMessage}")
            records.append(record)
            continue

        username = semantic.get("username")
        message = semantic.get("body")
        if type(username) is not str or type(message) is not str:
            raise RuntimeError(f"Persisted chat semantic line {lineNumber} has invalid username/body.")
        record["username"] = username
        record["body"] = message

        if kind == "generatedEvent":
            generated = semantic.get("event")
            if not isinstance(generated, dict):
                raise RuntimeError(f"Persisted generated chat event at line {lineNumber} is invalid.")
            # Batch reconstruction mutates recipient lists, so each window gets
            # its own detached event object rather than modifying the persisted
            # line-local semantic value.
            generated = {
                key: (list(value) if isinstance(value, list) else value)
                for key, value in generated.items()
            }
            eventType = generated["type"]
            analysis = {
                "kind": "generatedEvent",
                "event": generated,
                "includedInText": insideRequestedWindow,
                "streamTimeSeconds": float(streamTimeSeconds),
                "streamTime": streamTime,
            }
            record["analysis"] = analysis

            if eventType == "subscriptionGiftBatch":
                key = (username.casefold(), int(generated["tier"]))
                openBatches[key] = (float(streamTimeSeconds), generated, lineNumber)
                if insideRequestedWindow:
                    rendered.append(f"{streamTime} {username}: {_eventText(generated)}")
                records.append(record)
                continue

            if eventType == "subscriptionGift":
                key = (username.casefold(), int(generated["tier"]))
                batch = openBatches.get(key)
                if batch is not None:
                    openedAt, batchEvent, batchLine = batch
                    age = float(streamTimeSeconds) - openedAt
                    recipients = batchEvent.get("recipients")
                    expected = int(batchEvent.get("count", 0))
                    if 0 <= age <= _GIFT_BATCH_MAX_SECONDS and isinstance(recipients, list) and len(recipients) < expected:
                        recipients.append(generated["recipient"])
                        analysis["includedInText"] = False
                        analysis["partOfGiftBatchLineNumber"] = batchLine
                        if len(recipients) >= expected:
                            openBatches.pop(key, None)
                        records.append(record)
                        continue
                    openBatches.pop(key, None)

            if insideRequestedWindow:
                rendered.append(f"{streamTime} {username}: {_eventText(generated)}")
            records.append(record)
            continue

        if kind == "botEvent":
            botEvent = semantic.get("event")
            if not isinstance(botEvent, dict):
                raise RuntimeError(f"Persisted bot event at line {lineNumber} is invalid.")
            record["analysis"] = {
                "kind": "botEvent",
                "event": botEvent,
                "includedInText": False,
                "streamTimeSeconds": float(streamTimeSeconds),
                "streamTime": streamTime,
            }
            records.append(record)
            continue

        if kind != "userMessage":
            raise RuntimeError(f"Persisted chat semantic line {lineNumber} has unsupported kind {kind!r}.")

        spans = semantic.get("spans")
        if not isinstance(spans, list):
            raise RuntimeError(f"Persisted user chat semantic line {lineNumber} has invalid spans.")
        compactMessage = _renderSpans(spans)
        record["analysis"] = {
            "kind": "userMessage",
            "spans": spans,
            "includedInText": insideRequestedWindow,
            "streamTimeSeconds": float(streamTimeSeconds),
            "streamTime": streamTime,
        }
        if insideRequestedWindow:
            rendered.append(f"{streamTime} {username}: {compactMessage}")
        records.append(record)

    return {"records": records, "text": "\n".join(rendered)}


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.chatSemantics@1", _evaluate)
    ctx.capabilities.register("evilAnalysis.chatInterpret@1", _interpret)
