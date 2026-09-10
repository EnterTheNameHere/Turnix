# file: first-party/applications/evilBirthdayAnalysis/packs/chatSemantics/codeEntry.py ; version: 14
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping

_SEMANTIC_KEYS = ("semanticClass", "entity", "target")
_TRUSTED_CLASSIFICATION_SOURCE = "userDefined"
_GIFT_BATCH_MAX_SECONDS = 120
_UNICODE_TAG_START = 0xE0000
_UNICODE_TAG_END = 0xE007F

_SINGLE_GIFT_RE = re.compile(r"^(?P<sender>.+?) gifted a Tier (?P<tier>[123]) sub to (?P<recipient>.+)!$")
_BULK_GIFT_RE = re.compile(
    r"^(?P<sender>.+?) is gifting (?P<count>\d+) Tier (?P<tier>[123]) Subs to vedal987's community! "
    r"They've gifted a total of (?P<total>\d+) in the channel!$",
)
_TIMEOUT_RE = re.compile(r"^(?P<sender>.+?) has been timed out for (?P<seconds>\d+) seconds$")
_SUBSCRIPTION_RE = re.compile(
    r"^(?P<sender>.+?) subscribed (?P<method>with Prime|at Tier (?P<tier>[123]))\."
    r"(?: They've subscribed for (?P<months>\d+) months?"
    r"(?:, currently on a (?P<streak>\d+) month streak)?!)?"
    r"(?: (?P<message>.*))?$"
)
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

    observed = ctx.io.readObservedJson(path)
    definition = observed.get("value")
    after = observed.get("observation")
    if not isinstance(definition, dict) or not isinstance(after, dict):
        raise RuntimeError("Observed chat vocabulary read returned invalid data.")
    if not isinstance(definition, dict):
        raise ValueError("Chat emote vocabulary must be an object.")
    emotes = definition.get("emotes")
    composites = definition.get("composites", [])
    if not isinstance(emotes, dict) or not isinstance(composites, list):
        raise ValueError("Chat emote vocabulary requires emotes object and composites list.")

    normalizedEmotes: dict[str, dict[str, object]] = {}
    canonicalEmoteNames: dict[str, str] = {}
    for name, metadata in emotes.items():
        if type(name) is not str or not name or not isinstance(metadata, dict):
            raise ValueError("Chat emote definitions require non-empty string names and object metadata.")
        folded = name.casefold()
        if folded in canonicalEmoteNames:
            raise ValueError(
                "Chat emote definitions must be unique case-insensitively; "
                f"{canonicalEmoteNames[folded]!r} conflicts with {name!r}."
            )
        canonicalEmoteNames[folded] = name
        normalizedEmotes[folded] = {
            "name": name,
            "metadata": dict(metadata),
        }

    normalizedComposites: list[dict[str, object]] = []
    seenPatterns: set[tuple[str, ...]] = set()
    for compositeDefinition in composites:
        if not isinstance(compositeDefinition, dict):
            raise ValueError("Chat composite definitions must be objects.")
        tokens = compositeDefinition.get("tokens")
        if not isinstance(tokens, list) or len(tokens) < 2 or any(type(token) is not str or not token for token in tokens):
            raise ValueError("Chat composite definitions require at least two non-empty string tokens.")
        pattern = tuple(tokens)
        foldedPattern = tuple(token.casefold() for token in pattern)
        unknown = [token for token in pattern if token.casefold() not in normalizedEmotes]
        if unknown:
            raise ValueError(f"Chat composite references unknown emote token(s): {', '.join(unknown)}.")
        if foldedPattern in seenPatterns:
            raise ValueError(f"Duplicate chat composite pattern: {' '.join(pattern)}.")
        seenPatterns.add(foldedPattern)
        metadata = {key: value for key, value in compositeDefinition.items() if key != "tokens"}
        normalizedComposites.append(
            {
                "tokens": tuple(canonicalEmoteNames[token.casefold()] for token in pattern),
                "foldedTokens": foldedPattern,
                "metadata": metadata,
            }
        )

    normalizedComposites.sort(key=lambda item: len(item["tokens"]), reverse=True)
    return normalizedEmotes, normalizedComposites, after


def _isSemanticEdgeNoise(character: str) -> bool:
    codePoint = ord(character)
    return character.isspace() or _UNICODE_TAG_START <= codePoint <= _UNICODE_TAG_END


def _sanitizeSemanticBody(value: str) -> str:
    """Removes source-format edge noise only from the interpreted message body.

    Raw chat evidence remains byte-for-byte represented by rawLine/rawMessage.
    Unicode Tags block characters are default-ignorable source artifacts in the
    observed Twitch export and must not become lexical text spans merely because
    Python's ordinary whitespace splitting does not discard them.
    """
    start = 0
    end = len(value)
    while start < end and _isSemanticEdgeNoise(value[start]):
        start += 1
    while end > start and _isSemanticEdgeNoise(value[end - 1]):
        end -= 1
    return value[start:end]


def _splitUserMessage(value: str) -> tuple[str, str] | None:
    """Dynamically recognize the current username/body form without requiring it at ingestion."""
    separator = value.find(": ")
    if separator <= 0:
        return None
    username = value[:separator]
    if not username:
        return None
    return username, _sanitizeSemanticBody(value[separator + 2 :])


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
        pattern = composite["foldedTokens"]
        candidate = tuple(token.casefold() for token in tokens[index : index + len(pattern)])
        if candidate == pattern:
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
        emote = emotes.get(token.casefold())
        if emote is not None:
            count, nextIndex = _occurrenceCount(tokens, index + 1)
            _appendSpan(
                spans,
                {
                    "kind": "emote",
                    "name": emote["name"],
                    "count": count,
                    "metadata": dict(emote["metadata"]),
                },
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


def _subscriptionEvent(
    username: str,
    message: str,
) -> tuple[dict[str, object], str] | None:
    matched = _SUBSCRIPTION_RE.fullmatch(message)
    if matched is None or matched.group("sender").casefold() != username.casefold():
        return None

    method = matched.group("method")
    tier = matched.group("tier")
    months = matched.group("months")
    streak = matched.group("streak")
    authoredMessage = matched.group("message") or ""
    event: dict[str, object] = {
        "type": "subscription",
        "subscriber": matched.group("sender"),
        "method": "prime" if method == "with Prime" else "tier",
    }
    if tier is not None:
        event["tier"] = int(tier)
    if months is not None:
        event["monthsSubscribed"] = int(months)
    if streak is not None:
        event["streakMonths"] = int(streak)
    return event, authoredMessage


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


def _secondCellAddress(secondIndex: int) -> str:
    """Returns the stable address of one canonical one-second semantic bucket."""
    if type(secondIndex) is not int:
        raise TypeError("secondIndex must be an exact integer.")
    segment = f"n{-secondIndex}" if secondIndex < 0 else f"s{secondIndex}"
    return f"evilanalysis/chat/second/{segment}/semantic"


def _semanticReference(ctx, lineNumber: int) -> dict[str, object]:
    address = _semanticCellAddress(lineNumber)
    return {
        "address": address,
        "dependency": ctx.memory.dependency(address),
    }


def _secondBucketBasis(members: list[dict[str, object]]) -> dict[str, object]:
    """Returns ordered exact inputs that determine one second bucket."""
    return {
        "members": [
            {
                "lineNumber": member["lineNumber"],
                "streamTimeSeconds": member["streamTimeSeconds"],
                "semantic": member["semantic"],
            }
            for member in members
        ]
    }


def _persistentSecondBucket(
    ctx,
    *,
    secondIndex: int,
    members: list[dict[str, object]],
) -> dict[str, object]:
    """Returns one persistent canonical temporal bucket.

    A bucket preserves ordered semantic-line references and timing only. It
    performs no cross-message aggregation, gift reconstruction, identity
    anonymization, or model-facing presentation. Those remain higher layers.

    validity records the exact member/timing/dependency sequence. Dependency
    identities are stable before and after outer transaction commit, allowing
    this bucket to be derived transactionally from newly staged semantic lines
    without guessing their future authoritative revision numbers.
    """
    address = _secondCellAddress(secondIndex)
    basis = _secondBucketBasis(members)
    if ctx.memory.isReusable(address, validity=basis):
        bucket = ctx.memory.load(address)
        if isinstance(bucket, dict):
            return {
                "address": address,
                "dependency": ctx.memory.dependency(address),
                "value": bucket,
            }
        raise RuntimeError(f"Reusable chat second bucket at {address!r} is not an object.")

    value = {
        "secondIndex": secondIndex,
        "startSeconds": float(secondIndex),
        "endSeconds": float(secondIndex + 1),
        "members": members,
    }
    transaction = ctx.memory.openTransaction()
    transaction.set(
        address,
        value,
        validity=basis,
        provenance={
            "semanticInputs": [member["semantic"] for member in members],
            "lineNumbers": [member["lineNumber"] for member in members],
        },
    )
    transaction.commit()
    return {
        "address": address,
        "dependency": ctx.memory.dependency(address),
        "value": value,
    }


def _persistentSecondBuckets(
    ctx,
    records: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[int, list[dict[str, object]]] = {}
    for record in records:
        lineNumber = record.get("lineNumber")
        streamTimeSeconds = record.get("streamTimeSeconds")
        semanticValue = record.get("semanticValue")
        if (
            type(lineNumber) is not int
            or type(streamTimeSeconds) not in {int, float}
            or not isinstance(semanticValue, dict)
        ):
            raise RuntimeError("Interpreted chat record lacks semantic/timing evidence for second bucketing.")
        secondIndex = math.floor(float(streamTimeSeconds))
        grouped.setdefault(secondIndex, []).append(
            {
                "lineNumber": lineNumber,
                "streamTimeSeconds": float(streamTimeSeconds),
                "semantic": semanticValue,
            }
        )

    return [
        _persistentSecondBucket(ctx, secondIndex=secondIndex, members=grouped[secondIndex])
        for secondIndex in sorted(grouped)
    ]


def _secondAggregateAddress(secondIndex: int) -> str:
    """Returns the stable address of one structured same-second chat aggregate."""
    if type(secondIndex) is not int:
        raise TypeError("secondIndex must be an exact integer.")
    segment = f"n{-secondIndex}" if secondIndex < 0 else f"s{secondIndex}"
    return f"evilanalysis/chat/second/{segment}/aggregate"


def _canonicalUserMessage(semantic: dict[str, object]) -> tuple[str, list[dict[str, object]]] | None:
    if semantic.get("kind") != "userMessage":
        return None
    spans = semantic.get("spans")
    if not isinstance(spans, list) or any(not isinstance(span, dict) for span in spans):
        raise RuntimeError("Persistent user-message semantics require spans for aggregation.")
    canonicalKey = json.dumps(
        spans,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return canonicalKey, spans


def _secondAggregateValue(
    ctx,
    *,
    secondIndex: int,
    bucket: dict[str, object],
) -> dict[str, object]:
    """Builds neutral same-second structure without model-facing policy."""
    value = bucket.get("value")
    if not isinstance(value, dict) or not isinstance(value.get("members"), list):
        raise RuntimeError("Second aggregate requires a canonical second bucket.")
    members = value["members"]

    grouped: dict[str, list[dict[str, object]]] = {}
    canonicalSpans: dict[str, list[dict[str, object]]] = {}
    semanticByLine: dict[int, dict[str, object]] = {}
    for member in members:
        if not isinstance(member, dict):
            raise RuntimeError("Second bucket member must be an object.")
        lineNumber = member.get("lineNumber")
        semanticReference = member.get("semantic")
        if type(lineNumber) is not int or not isinstance(semanticReference, dict):
            raise RuntimeError("Second bucket member lacks semantic reference.")
        semanticAddress = semanticReference.get("address")
        if type(semanticAddress) is not str:
            raise RuntimeError("Second bucket semantic reference lacks address.")
        semantic = ctx.memory.load(semanticAddress)
        if not isinstance(semantic, dict):
            raise RuntimeError(f"Second aggregate semantic input {semanticAddress!r} is unavailable.")
        semanticByLine[lineNumber] = semantic

        canonical = _canonicalUserMessage(semantic)
        if canonical is None:
            continue
        key, spans = canonical
        grouped.setdefault(key, []).append(member)
        canonicalSpans[key] = spans

    claimed: set[int] = set()
    entries: list[dict[str, object]] = []
    for member in members:
        lineNumber = member["lineNumber"]
        if lineNumber in claimed:
            continue
        semantic = semanticByLine[lineNumber]
        canonical = _canonicalUserMessage(semantic)
        if canonical is None:
            entries.append(
                {
                    "kind": "message",
                    "lineNumber": lineNumber,
                    "semantic": member["semantic"],
                }
            )
            claimed.add(lineNumber)
            continue

        key, _spans = canonical
        group = grouped[key]
        if len(group) == 1:
            entries.append(
                {
                    "kind": "message",
                    "lineNumber": lineNumber,
                    "semantic": member["semantic"],
                }
            )
            claimed.add(lineNumber)
            continue

        sourceUsernames: list[str] = []
        for groupMember in group:
            groupLineNumber = groupMember["lineNumber"]
            groupSemantic = semanticByLine[groupLineNumber]
            username = groupSemantic.get("username")
            if type(username) is not str or not username:
                raise RuntimeError("Canonical user-message group lacks source username.")
            sourceUsernames.append(username)

        groupLineNumbers = [groupMember["lineNumber"] for groupMember in group]
        claimed.update(groupLineNumbers)
        entries.append(
            {
                "kind": "identicalCanonicalMessage",
                "canonicalMessage": _renderSpans(canonicalSpans[key]),
                "canonicalSpans": canonicalSpans[key],
                "lineNumbers": groupLineNumbers,
                "semantic": [groupMember["semantic"] for groupMember in group],
                "sourceUsernames": sourceUsernames,
                "messageCount": len(group),
                "uniqueSourceUserCount": len({username.casefold() for username in sourceUsernames}),
            }
        )

    return {
        "secondIndex": secondIndex,
        "secondBucket": {
            "address": bucket["address"],
            "dependency": bucket["dependency"],
        },
        "entries": entries,
    }


def _persistentSecondAggregate(
    ctx,
    *,
    bucket: dict[str, object],
) -> dict[str, object]:
    value = bucket.get("value")
    dependency = bucket.get("dependency")
    if not isinstance(value, dict) or not isinstance(dependency, dict):
        raise RuntimeError("Second aggregate requires bucket value and dependency.")
    secondIndex = value.get("secondIndex")
    if type(secondIndex) is not int:
        raise RuntimeError("Second bucket lacks exact secondIndex.")

    address = _secondAggregateAddress(secondIndex)
    basis = {"secondBucket": dependency}
    if ctx.memory.isReusable(address, validity=basis):
        aggregate = ctx.memory.load(address)
        if isinstance(aggregate, dict):
            return {
                "address": address,
                "dependency": ctx.memory.dependency(address),
                "value": aggregate,
            }
        raise RuntimeError(f"Reusable second aggregate at {address!r} is not an object.")

    aggregate = _secondAggregateValue(ctx, secondIndex=secondIndex, bucket=bucket)
    transaction = ctx.memory.openTransaction()
    transaction.set(
        address,
        aggregate,
        validity=basis,
        provenance={
            "secondBucket": {
                "address": bucket["address"],
                "dependency": dependency,
            }
        },
    )
    transaction.commit()
    return {
        "address": address,
        "dependency": ctx.memory.dependency(address),
        "value": aggregate,
    }


def _persistentSecondAggregates(
    ctx,
    buckets: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [_persistentSecondAggregate(ctx, bucket=bucket) for bucket in buckets]


def _burstSecondSegment(secondIndex: int) -> str:
    return f"n{-secondIndex}" if secondIndex < 0 else f"s{secondIndex}"


def _burstStartCellAddress(startSecond: int) -> str:
    """Returns the stable logical slot for bursts proven to start in one second."""
    if type(startSecond) is not int:
        raise TypeError("startSecond must be an exact integer.")
    return f"evilanalysis/chat/burst/{_burstSecondSegment(startSecond)}"


def _aggregateCanonicalOccurrences(
    ctx,
    aggregate: dict[str, object],
) -> tuple[int, dict[str, list[dict[str, object]]], dict[str, list[dict[str, object]]]]:
    """Returns canonical user-message occurrences for one persistent second aggregate."""
    value = aggregate.get("value")
    if not isinstance(value, dict):
        raise RuntimeError("Burst derivation requires second aggregate value.")
    secondIndex = value.get("secondIndex")
    secondBucket = value.get("secondBucket")
    entries = value.get("entries")
    if (
        type(secondIndex) is not int
        or not isinstance(secondBucket, dict)
        or not isinstance(entries, list)
    ):
        raise RuntimeError("Second aggregate has invalid burst input structure.")

    bucketAddress = secondBucket.get("address")
    if type(bucketAddress) is not str:
        raise RuntimeError("Second aggregate lacks second-bucket address.")
    bucketValue = ctx.memory.load(bucketAddress)
    if not isinstance(bucketValue, dict) or not isinstance(bucketValue.get("members"), list):
        raise RuntimeError("Second aggregate references unavailable bucket.")
    timeByLine = {
        member["lineNumber"]: member["streamTimeSeconds"]
        for member in bucketValue["members"]
        if (
            isinstance(member, dict)
            and type(member.get("lineNumber")) is int
            and type(member.get("streamTimeSeconds")) in {int, float}
        )
    }

    occurrencesByCanonical: dict[str, list[dict[str, object]]] = {}
    spansByCanonical: dict[str, list[dict[str, object]]] = {}

    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("Second aggregate entry must be an object.")
        kind = entry.get("kind")

        if kind == "message":
            lineNumber = entry.get("lineNumber")
            semanticReference = entry.get("semantic")
            if type(lineNumber) is not int or not isinstance(semanticReference, dict):
                raise RuntimeError("Second aggregate message entry is invalid.")
            semanticAddress = semanticReference.get("address")
            if type(semanticAddress) is not str:
                raise RuntimeError("Second aggregate message semantic reference lacks address.")
            semantic = ctx.memory.load(semanticAddress)
            if not isinstance(semantic, dict):
                raise RuntimeError("Second aggregate message semantic value is unavailable.")
            canonical = _canonicalUserMessage(semantic)
            if canonical is None:
                continue
            canonicalKey, spans = canonical
            username = semantic.get("username")
            if type(username) is not str or not username:
                raise RuntimeError("Canonical user message lacks username.")
            streamTimeSeconds = timeByLine.get(lineNumber)
            if type(streamTimeSeconds) not in {int, float}:
                raise RuntimeError("Canonical user message lacks bucket timing evidence.")
            occurrencesByCanonical.setdefault(canonicalKey, []).append(
                {
                    "lineNumber": lineNumber,
                    "secondIndex": secondIndex,
                    "streamTimeSeconds": float(streamTimeSeconds),
                    "sourceUsername": username,
                    "semantic": semanticReference,
                }
            )
            spansByCanonical[canonicalKey] = spans
            continue

        if kind == "identicalCanonicalMessage":
            spans = entry.get("canonicalSpans")
            lineNumbers = entry.get("lineNumbers")
            semanticReferences = entry.get("semantic")
            sourceUsernames = entry.get("sourceUsernames")
            if (
                not isinstance(spans, list)
                or any(not isinstance(span, dict) for span in spans)
                or not isinstance(lineNumbers, list)
                or not isinstance(semanticReferences, list)
                or not isinstance(sourceUsernames, list)
                or not (
                    len(lineNumbers)
                    == len(semanticReferences)
                    == len(sourceUsernames)
                )
            ):
                raise RuntimeError("Identical canonical-message entry is invalid.")
            canonicalKey = json.dumps(
                spans,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            for lineNumber, semanticReference, sourceUsername in zip(
                lineNumbers,
                semanticReferences,
                sourceUsernames,
                strict=True,
            ):
                if (
                    type(lineNumber) is not int
                    or not isinstance(semanticReference, dict)
                    or type(sourceUsername) is not str
                    or not sourceUsername
                ):
                    raise RuntimeError("Identical canonical-message occurrence is invalid.")
                streamTimeSeconds = timeByLine.get(lineNumber)
                if type(streamTimeSeconds) not in {int, float}:
                    raise RuntimeError("Identical canonical-message occurrence lacks timing.")
                occurrencesByCanonical.setdefault(canonicalKey, []).append(
                    {
                        "lineNumber": lineNumber,
                        "secondIndex": secondIndex,
                        "streamTimeSeconds": float(streamTimeSeconds),
                        "sourceUsername": sourceUsername,
                        "semantic": semanticReference,
                    }
                )
            spansByCanonical[canonicalKey] = spans
            continue

        raise RuntimeError(
            f"Unsupported second aggregate entry kind for burst derivation: {kind!r}.",
        )

    return secondIndex, occurrencesByCanonical, spansByCanonical


def _canonicalRuns(
    secondsByCanonical: dict[str, dict[int, list[dict[str, object]]]],
) -> list[tuple[str, list[int]]]:
    """Returns maximal consecutive-second runs present in the supplied coverage."""
    runs: list[tuple[str, list[int]]] = []
    for canonicalKey, bySecond in secondsByCanonical.items():
        orderedSeconds = sorted(bySecond)
        if not orderedSeconds:
            continue
        current = [orderedSeconds[0]]
        for secondIndex in orderedSeconds[1:]:
            if secondIndex == current[-1] + 1:
                current.append(secondIndex)
            else:
                runs.append((canonicalKey, current))
                current = [secondIndex]
        runs.append((canonicalKey, current))
    return runs


def _aggregateInputSignature(
    aggregateBySecond: dict[int, dict[str, object]],
    secondIndex: int,
) -> dict[str, object]:
    aggregate = aggregateBySecond.get(secondIndex)
    if aggregate is None:
        return {
            "secondIndex": secondIndex,
            "aggregate": None,
        }
    dependency = aggregate.get("dependency")
    address = aggregate.get("address")
    if type(address) is not str or not isinstance(dependency, dict):
        raise RuntimeError("Burst input aggregate reference is incomplete.")
    return {
        "secondIndex": secondIndex,
        "aggregate": {
            "address": address,
            "dependency": dependency,
        },
    }


def _burstEventValue(
    *,
    canonicalKey: str,
    canonicalSpans: list[dict[str, object]],
    runSeconds: list[int],
    occurrencesBySecond: dict[int, list[dict[str, object]]],
    aggregateBySecond: dict[int, dict[str, object]],
) -> dict[str, object]:
    canonicalSha256 = hashlib.sha256(canonicalKey.encode("utf-8")).hexdigest()
    occurrences = [
        occurrence
        for secondIndex in runSeconds
        for occurrence in occurrencesBySecond[secondIndex]
    ]
    sourceUsernames = [occurrence["sourceUsername"] for occurrence in occurrences]
    return {
        "eventKey": canonicalSha256,
        "kind": "identicalMessageBurst",
        "canonicalSha256": canonicalSha256,
        "canonicalMessage": _renderSpans(canonicalSpans),
        "canonicalSpans": canonicalSpans,
        "startSecond": runSeconds[0],
        "endSecond": runSeconds[-1],
        "durationSeconds": runSeconds[-1] - runSeconds[0] + 1,
        "messageCount": len(occurrences),
        "uniqueSourceUserCount": len(
            {username.casefold() for username in sourceUsernames}
        ),
        "peakMessagesPerSecond": max(
            len(occurrencesBySecond[secondIndex])
            for secondIndex in runSeconds
        ),
        "sourceUsernames": sourceUsernames,
        "occurrences": occurrences,
        "seconds": [
            {
                "secondIndex": secondIndex,
                "messageCount": len(occurrencesBySecond[secondIndex]),
                "uniqueSourceUserCount": len(
                    {
                        occurrence["sourceUsername"].casefold()
                        for occurrence in occurrencesBySecond[secondIndex]
                    }
                ),
                "aggregate": {
                    "address": aggregateBySecond[secondIndex]["address"],
                    "dependency": aggregateBySecond[secondIndex]["dependency"],
                },
            }
            for secondIndex in runSeconds
        ],
    }


def _persistentBurstStartCell(
    ctx,
    *,
    startSecond: int,
    events: list[dict[str, object]] | None,
    inputEndSecond: int,
    aggregateBySecond: dict[int, dict[str, object]],
) -> dict[str, object] | None:
    """Publishes one stable burst-start slot or marks it currently indeterminate.

    events=None means the start is proven but at least one burst beginning here
    reaches beyond supplied right-hand coverage, so any prior PRESENT value is
    no longer trustworthy. An empty events list is authoritative evidence that
    no identical-message burst starts in this second.
    """
    address = _burstStartCellAddress(startSecond)
    signatures = [
        _aggregateInputSignature(aggregateBySecond, secondIndex)
        for secondIndex in range(startSecond - 1, inputEndSecond + 2)
    ]
    basis = {"secondInputs": signatures}

    if events is None:
        if not ctx.memory.isCurrent(address, state="invalidated", validity=basis):
            transaction = ctx.memory.openTransaction()
            transaction.invalidate(
                address,
                validity=basis,
                provenance={
                    "reason": "burstEndOutsideObservedCoverage",
                    "secondInputs": signatures,
                },
            )
            transaction.commit()
        return None

    orderedEvents = sorted(events, key=lambda event: str(event["eventKey"]))
    value = {
        "startSecond": startSecond,
        "events": orderedEvents,
    }
    if ctx.memory.isReusable(address, validity=basis):
        current = ctx.memory.load(address)
        if isinstance(current, dict):
            return {
                "address": address,
                "dependency": ctx.memory.dependency(address),
                "value": current,
            }
        raise RuntimeError(f"Reusable burst-start cell at {address!r} is not an object.")

    transaction = ctx.memory.openTransaction()
    transaction.set(
        address,
        value,
        validity=basis,
        provenance={
            "secondInputs": signatures,
        },
    )
    transaction.commit()
    return {
        "address": address,
        "dependency": ctx.memory.dependency(address),
        "value": value,
    }


def _persistentIdenticalMessageBursts(
    ctx,
    aggregates: list[dict[str, object]],
    *,
    contextStartSeconds: float,
    contextEndSeconds: float,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Materializes stable per-second burst-start slots and closed event refs."""
    aggregateBySecond: dict[int, dict[str, object]] = {}
    secondsByCanonical: dict[str, dict[int, list[dict[str, object]]]] = {}
    spansByCanonical: dict[str, list[dict[str, object]]] = {}

    for aggregate in aggregates:
        if not isinstance(aggregate, dict):
            raise RuntimeError("Burst derivation requires aggregate objects.")
        secondIndex, occurrences, canonicalSpans = _aggregateCanonicalOccurrences(
            ctx,
            aggregate,
        )
        aggregateBySecond[secondIndex] = aggregate
        for canonicalKey, items in occurrences.items():
            secondsByCanonical.setdefault(canonicalKey, {})[secondIndex] = items
        spansByCanonical.update(canonicalSpans)

    eventRunsByStart: dict[int, list[tuple[str, list[int]]]] = {}
    openStarts: set[int] = set()
    for canonicalKey, runSeconds in _canonicalRuns(secondsByCanonical):
        if len(runSeconds) < 2:
            continue
        startSecond = runSeconds[0]
        endSecond = runSeconds[-1]

        # If the preceding second is not fully observed, we cannot prove this
        # second is the logical start slot and must not publish it.
        if contextStartSeconds > float(startSecond - 1):
            continue

        if contextEndSeconds < float(endSecond + 2):
            openStarts.add(startSecond)
            continue

        eventRunsByStart.setdefault(startSecond, []).append(
            (canonicalKey, runSeconds),
        )

    firstEligibleStart = math.ceil(contextStartSeconds + 1)
    lastEligibleStart = math.floor(contextEndSeconds - 2)
    burstStartCells: list[dict[str, object]] = []
    closedEvents: list[dict[str, object]] = []

    for startSecond in range(firstEligibleStart, lastEligibleStart + 1):
        runs = eventRunsByStart.get(startSecond, [])
        if startSecond in openStarts:
            # Include every observed second through the coverage edge so a
            # changed partial run changes invalidation validity.
            inputEndSecond = max(
                runSeconds[-1]
                for canonicalKey, runSeconds in _canonicalRuns(secondsByCanonical)
                if runSeconds[0] == startSecond and len(runSeconds) >= 2
            )
            _persistentBurstStartCell(
                ctx,
                startSecond=startSecond,
                events=None,
                inputEndSecond=inputEndSecond,
                aggregateBySecond=aggregateBySecond,
            )
            continue

        events = [
            _burstEventValue(
                canonicalKey=canonicalKey,
                canonicalSpans=spansByCanonical[canonicalKey],
                runSeconds=runSeconds,
                occurrencesBySecond=secondsByCanonical[canonicalKey],
                aggregateBySecond=aggregateBySecond,
            )
            for canonicalKey, runSeconds in runs
        ]
        inputEndSecond = max(
            [startSecond + 1, *(event["endSecond"] for event in events)],
        )
        cell = _persistentBurstStartCell(
            ctx,
            startSecond=startSecond,
            events=events,
            inputEndSecond=inputEndSecond,
            aggregateBySecond=aggregateBySecond,
        )
        if cell is None:
            continue
        burstStartCells.append(cell)
        for event in cell["value"]["events"]:
            closedEvents.append(
                {
                    "address": cell["address"],
                    "dependency": cell["dependency"],
                    "eventKey": event["eventKey"],
                    "value": event,
                }
            )

    return burstStartCells, closedEvents


def _secondPresentationAddress(secondIndex: int) -> str:
    """Returns the stable persistent presentation-plan address for one second."""
    if type(secondIndex) is not int:
        raise TypeError("secondIndex must be an exact integer.")
    return f"evilanalysis/chat/second/{_burstSecondSegment(secondIndex)}/presentation"


def _repeatPresentation(semantic: dict[str, object]) -> dict[str, object] | None:
    if semantic.get("kind") != "userMessage":
        return None
    spans = semantic.get("spans")
    if (
        not isinstance(spans, list)
        or len(spans) != 1
        or not isinstance(spans[0], dict)
        or spans[0].get("kind") != "repeat"
    ):
        return None
    repeat = spans[0]
    count = repeat.get("count")
    nested = repeat.get("spans")
    if (
        type(count) is not int
        or count <= 1
        or not isinstance(nested, list)
        or not nested
        or any(not isinstance(span, dict) for span in nested)
    ):
        raise RuntimeError("Persisted repeat semantic span has invalid presentation shape.")
    return {
        "kind": "repeatMessage",
        "count": count,
        "spans": nested,
        "renderedUnit": _renderSpans(nested),
        "rendered": _renderSpans(spans),
    }


def _semanticUnitPresentation(
    semantic: dict[str, object],
) -> list[dict[str, object]] | None:
    """Returns trusted semantic units only when the whole message is reducible."""
    if semantic.get("kind") != "userMessage":
        return None
    spans = semantic.get("spans")
    if not isinstance(spans, list):
        raise RuntimeError("Persisted user-message semantics require spans.")
    evaluated = _evaluate(None, {"spans": spans})
    if evaluated.get("aggregationEligible") is not True:
        return None
    units = evaluated.get("semanticUnits")
    if not isinstance(units, list) or not units:
        return None
    normalizedByMeaning: dict[str, dict[str, object]] = {}
    for unit in units:
        if not isinstance(unit, dict):
            raise RuntimeError("Semantic evaluation returned invalid unit.")
        meaning = unit.get("meaning")
        count = unit.get("count")
        if not isinstance(meaning, dict) or type(count) is not int or count <= 0:
            raise RuntimeError("Semantic evaluation returned invalid unit evidence.")
        key = _meaningKey(meaning)
        normalized = normalizedByMeaning.setdefault(
            key,
            {"meaning": meaning, "count": 0},
        )
        normalized["count"] = int(normalized["count"]) + count
    return [
        normalizedByMeaning[key]
        for key in sorted(normalizedByMeaning)
    ]


def _meaningKey(meaning: dict[str, object]) -> str:
    return json.dumps(
        meaning,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _burstMembershipByLine(
    bursts: list[dict[str, object]],
) -> dict[int, dict[str, object]]:
    byLine: dict[int, dict[str, object]] = {}
    for burst in bursts:
        if not isinstance(burst, dict):
            raise RuntimeError("Presentation planning requires burst references.")
        address = burst.get("address")
        dependency = burst.get("dependency")
        eventKey = burst.get("eventKey")
        value = burst.get("value")
        if (
            type(address) is not str
            or not isinstance(dependency, dict)
            or type(eventKey) is not str
            or not isinstance(value, dict)
        ):
            raise RuntimeError("Presentation planning received incomplete burst reference.")
        occurrences = value.get("occurrences")
        if not isinstance(occurrences, list):
            raise RuntimeError("Presentation planning burst lacks occurrences.")
        reference = {
            "address": address,
            "dependency": dependency,
            "eventKey": eventKey,
        }
        for occurrence in occurrences:
            if not isinstance(occurrence, dict) or type(occurrence.get("lineNumber")) is not int:
                raise RuntimeError("Presentation planning burst occurrence lacks lineNumber.")
            lineNumber = occurrence["lineNumber"]
            if lineNumber in byLine:
                raise RuntimeError(f"Chat line {lineNumber} has multiple burst presentation owners.")
            byLine[lineNumber] = reference
    return byLine


def _secondPresentationValue(
    ctx,
    *,
    aggregate: dict[str, object],
    burstByLine: dict[int, dict[str, object]],
) -> dict[str, object]:
    aggregateValue = aggregate.get("value")
    if not isinstance(aggregateValue, dict):
        raise RuntimeError("Presentation planning requires second aggregate value.")
    secondIndex = aggregateValue.get("secondIndex")
    secondBucket = aggregateValue.get("secondBucket")
    if type(secondIndex) is not int or not isinstance(secondBucket, dict):
        raise RuntimeError("Presentation planning second aggregate is incomplete.")
    bucketAddress = secondBucket.get("address")
    if type(bucketAddress) is not str:
        raise RuntimeError("Presentation planning aggregate lacks bucket address.")
    bucketValue = ctx.memory.load(bucketAddress)
    if not isinstance(bucketValue, dict) or not isinstance(bucketValue.get("members"), list):
        raise RuntimeError("Presentation planning aggregate references unavailable bucket.")

    entries: list[dict[str, object]] = []
    semanticGroups: dict[str, dict[str, object]] = {}

    aggregateEntries = aggregateValue.get("entries")
    if not isinstance(aggregateEntries, list):
        raise RuntimeError("Presentation planning aggregate requires structured entries.")
    identicalGroupByLine: dict[int, dict[str, object]] = {}
    for aggregateEntry in aggregateEntries:
        if not isinstance(aggregateEntry, dict):
            raise RuntimeError("Presentation planning aggregate entry must be an object.")
        if aggregateEntry.get("kind") != "identicalCanonicalMessage":
            continue
        lineNumbers = aggregateEntry.get("lineNumbers")
        if (
            not isinstance(lineNumbers, list)
            or len(lineNumbers) < 2
            or any(type(lineNumber) is not int for lineNumber in lineNumbers)
        ):
            raise RuntimeError("Identical-message aggregate has invalid line membership.")
        for lineNumber in lineNumbers:
            if lineNumber in identicalGroupByLine:
                raise RuntimeError(f"Chat line {lineNumber} belongs to multiple identical-message groups.")
            identicalGroupByLine[lineNumber] = aggregateEntry

    emittedIdenticalGroups: set[tuple[int, ...]] = set()

    for member in bucketValue["members"]:
        if not isinstance(member, dict):
            raise RuntimeError("Presentation planning bucket member must be an object.")
        lineNumber = member.get("lineNumber")
        semanticReference = member.get("semantic")
        if type(lineNumber) is not int or not isinstance(semanticReference, dict):
            raise RuntimeError("Presentation planning member lacks semantic reference.")

        burst = burstByLine.get(lineNumber)
        if burst is not None:
            entries.append(
                {
                    "kind": "burstOccurrence",
                    "lineNumber": lineNumber,
                    "burst": burst,
                }
            )
            continue

        identicalGroup = identicalGroupByLine.get(lineNumber)
        if identicalGroup is not None:
            groupLineNumbers = tuple(identicalGroup["lineNumbers"])
            if groupLineNumbers not in emittedIdenticalGroups:
                emittedIdenticalGroups.add(groupLineNumbers)
                entries.append(
                    {
                        "kind": "identicalMessageGroup",
                        "canonicalMessage": identicalGroup.get("canonicalMessage"),
                        "canonicalSpans": identicalGroup.get("canonicalSpans"),
                        "lineNumbers": list(groupLineNumbers),
                        "semantic": identicalGroup.get("semantic"),
                        "sourceUsernames": identicalGroup.get("sourceUsernames"),
                        "messageCount": identicalGroup.get("messageCount"),
                        "uniqueSourceUserCount": identicalGroup.get("uniqueSourceUserCount"),
                    }
                )
            continue

        semanticAddress = semanticReference.get("address")
        if type(semanticAddress) is not str:
            raise RuntimeError("Presentation planning semantic reference lacks address.")
        semantic = ctx.memory.load(semanticAddress)
        if not isinstance(semantic, dict):
            raise RuntimeError("Presentation planning semantic value is unavailable.")

        repeat = _repeatPresentation(semantic)
        if repeat is not None:
            entries.append(
                {
                    **repeat,
                    "lineNumber": lineNumber,
                    "semantic": semanticReference,
                }
            )
            continue

        units = _semanticUnitPresentation(semantic)
        if units is not None:
            for unit in units:
                meaning = unit["meaning"]
                key = _meaningKey(meaning)
                group = semanticGroups.setdefault(
                    key,
                    {
                        "kind": "semanticUnitGroup",
                        "meaning": meaning,
                        "count": 0,
                        "members": [],
                    },
                )
                group["count"] = int(group["count"]) + int(unit["count"])
                group["members"].append(
                    {
                        "lineNumber": lineNumber,
                        "count": int(unit["count"]),
                        "semantic": semanticReference,
                    }
                )
            continue

        entries.append(
            {
                "kind": "individual",
                "lineNumber": lineNumber,
                "semantic": semanticReference,
            }
        )

    entries.extend(
        semanticGroups[key]
        for key in sorted(semanticGroups)
    )
    return {
        "secondIndex": secondIndex,
        "entries": entries,
    }


def _persistentSecondPresentation(
    ctx,
    *,
    aggregate: dict[str, object],
    burstByLine: dict[int, dict[str, object]],
) -> dict[str, object]:
    value = aggregate.get("value")
    dependency = aggregate.get("dependency")
    address = aggregate.get("address")
    if (
        not isinstance(value, dict)
        or type(value.get("secondIndex")) is not int
        or not isinstance(dependency, dict)
        or type(address) is not str
    ):
        raise RuntimeError("Presentation planning requires complete aggregate reference.")
    secondIndex = value["secondIndex"]

    secondBucket = value.get("secondBucket")
    if not isinstance(secondBucket, dict) or type(secondBucket.get("address")) is not str:
        raise RuntimeError("Presentation planning aggregate lacks second-bucket reference.")
    bucketValue = ctx.memory.load(secondBucket["address"])
    if not isinstance(bucketValue, dict) or not isinstance(bucketValue.get("members"), list):
        raise RuntimeError("Presentation planning aggregate references unavailable second bucket.")
    bucketLineNumbers = {
        member["lineNumber"]
        for member in bucketValue["members"]
        if isinstance(member, dict) and type(member.get("lineNumber")) is int
    }
    if len(bucketLineNumbers) != len(bucketValue["members"]):
        raise RuntimeError("Presentation planning bucket contains invalid or duplicate line membership.")

    relevantBursts = sorted(
        (
            {
                "lineNumber": lineNumber,
                "burst": burst,
            }
            for lineNumber, burst in burstByLine.items()
            if lineNumber in bucketLineNumbers
        ),
        key=lambda item: item["lineNumber"],
    )
    basis = {
        "secondAggregate": {
            "address": address,
            "dependency": dependency,
        },
        "burstMemberships": relevantBursts,
    }
    planAddress = _secondPresentationAddress(secondIndex)
    if ctx.memory.isReusable(planAddress, validity=basis):
        current = ctx.memory.load(planAddress)
        if isinstance(current, dict):
            return {
                "address": planAddress,
                "dependency": ctx.memory.dependency(planAddress),
                "value": current,
            }
        raise RuntimeError(f"Reusable presentation plan at {planAddress!r} is not an object.")

    plan = _secondPresentationValue(
        ctx,
        aggregate=aggregate,
        burstByLine=burstByLine,
    )
    transaction = ctx.memory.openTransaction()
    transaction.set(
        planAddress,
        plan,
        validity=basis,
        provenance={
            "secondAggregate": {
                "address": address,
                "dependency": dependency,
            },
            "burstMemberships": relevantBursts,
        },
    )
    transaction.commit()
    return {
        "address": planAddress,
        "dependency": ctx.memory.dependency(planAddress),
        "value": plan,
    }


def _persistentSecondPresentations(
    ctx,
    aggregates: list[dict[str, object]],
    bursts: list[dict[str, object]],
) -> list[dict[str, object]]:
    burstByLine = _burstMembershipByLine(bursts)
    return [
        _persistentSecondPresentation(
            ctx,
            aggregate=aggregate,
            burstByLine=burstByLine,
        )
        for aggregate in aggregates
    ]


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
    subscription = _subscriptionEvent(username, message)
    if subscription is not None:
        platformEvent, authoredMessage = subscription
        if authoredMessage:
            return {
                "kind": "userMessage",
                "username": username,
                "body": authoredMessage,
                "spans": _lexMessage(authoredMessage, emotes, composites),
                "platformEvent": platformEvent,
            }
        return {
            "kind": "generatedEvent",
            "username": username,
            "body": message,
            "event": platformEvent,
        }

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
        "vocabularyContentSha256": vocabularyObservation.get("contentSha256"),
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
) -> tuple[dict[str, object], dict[str, object]]:
    """Returns current line semantics plus its persistent dependency reference.

    The logical address remains stable when source or processing changes.
    Memory-managed revisioning records successive authoritative states. The
    Pack owns the domain validity rule: exact raw input and vocabulary content
    identity must still match. Actant automatically compares the persisted
    producer identity with the currently executing CodeEntry implementation.

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
    if ctx.memory.isReusable(address, validity=basis):
        semantic = ctx.memory.load(address)
        if isinstance(semantic, dict):
            return semantic, _semanticReference(ctx, lineNumber)
        raise RuntimeError(f"Reusable semantic Value at {address!r} is not an object.")

    semantic = _lineSemantic(
        rawMessage,
        emotes=emotes,
        composites=composites,
    )
    transaction = ctx.memory.openTransaction()
    transaction.set(
        address,
        semantic,
        validity=basis,
        provenance={
            "source": {
                "path": sourcePath,
                "observation": sourceObservation,
                "lineNumber": lineNumber,
            },
            "vocabularyObservation": vocabularyObservation,
        },
    )
    transaction.commit()
    return semantic, _semanticReference(ctx, lineNumber)


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
    contextStartSeconds = payload.get("contextStreamStartSeconds")
    contextEndSeconds = payload.get("contextStreamEndSeconds")
    if not isinstance(rawRecords, list):
        raise TypeError("Chat interpretation requires a records list.")
    if type(sourcePath) is not str or not sourcePath:
        raise TypeError("Chat interpretation requires sourcePath.")
    if not isinstance(sourceObservation, dict):
        raise TypeError("Chat interpretation requires sourceObservation.")
    if (
        type(contextStartSeconds) not in {int, float}
        or type(contextEndSeconds) not in {int, float}
    ):
        raise TypeError("Chat interpretation requires numeric context coverage bounds.")
    contextStartSeconds = float(contextStartSeconds)
    contextEndSeconds = float(contextEndSeconds)
    if (
        not math.isfinite(contextStartSeconds)
        or not math.isfinite(contextEndSeconds)
        or contextEndSeconds < contextStartSeconds
    ):
        raise ValueError("Chat interpretation context coverage bounds are invalid.")

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

        semantic, semanticValue = _persistentLineSemantic(
            ctx,
            record,
            emotes=emotes,
            composites=composites,
            vocabularyObservation=vocabularyObservation,
            sourcePath=sourcePath,
            sourceObservation=sourceObservation,
        )
        record["semanticValue"] = semanticValue
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
        analysis = {
            "kind": "userMessage",
            "spans": spans,
            "includedInText": insideRequestedWindow,
            "streamTimeSeconds": float(streamTimeSeconds),
            "streamTime": streamTime,
        }
        platformEvent = semantic.get("platformEvent")
        if platformEvent is not None:
            if not isinstance(platformEvent, dict):
                raise RuntimeError(
                    f"Persisted user chat semantic line {lineNumber} has invalid platformEvent."
                )
            analysis["platformEvent"] = platformEvent
        record["analysis"] = analysis
        if insideRequestedWindow:
            rendered.append(f"{streamTime} {username}: {compactMessage}")
        records.append(record)

    secondBuckets = _persistentSecondBuckets(ctx, records)
    secondAggregates = _persistentSecondAggregates(ctx, secondBuckets)
    burstStartCells, identicalMessageBursts = _persistentIdenticalMessageBursts(
        ctx,
        secondAggregates,
        contextStartSeconds=contextStartSeconds,
        contextEndSeconds=contextEndSeconds,
    )
    secondPresentations = _persistentSecondPresentations(
        ctx,
        secondAggregates,
        identicalMessageBursts,
    )
    return {
        "records": records,
        "secondBuckets": secondBuckets,
        "secondAggregates": secondAggregates,
        "burstStartCells": burstStartCells,
        "identicalMessageBursts": identicalMessageBursts,
        "secondPresentations": secondPresentations,
        "text": "\n".join(rendered),
    }


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.chatSemantics@1", _evaluate)
    ctx.capabilities.register("evilAnalysis.chatInterpret@1", _interpret)
