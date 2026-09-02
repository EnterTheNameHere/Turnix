from __future__ import annotations

import importlib.util
from pathlib import Path

_IMPLEMENTATION_PATH = Path(__file__).with_name("_implementation.py")
_SPEC = importlib.util.spec_from_file_location("evilBirthdayAnalysisImplementation", _IMPLEMENTATION_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Could not load Evil Birthday analysis implementation: {_IMPLEMENTATION_PATH}")
_implementation = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_implementation)

# Re-export the implementation surface. Tests and pack users intentionally treat
# these helpers as the analysis pack's implementation API while the wrapper lets
# us replace focused hot paths and boundary adapters without rewriting the large
# source module in place.
for _name, _value in vars(_implementation).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


_CHAT_SEMANTIC_LOOKBACK_SECONDS = 120
_UNCLASSIFIED_CHAT_AUTHOR = "[unclassified]"


def _interpretedChat(ctx, selector: dict[str, int]) -> tuple[dict[str, object], dict[str, object]]:
    """Selects raw chat with semantic lookback, then interprets it through the semantics Pack."""
    rawChat = ctx.capabilities.call(
        "evilAnalysis.chat@1",
        {**selector, "lookbackSeconds": _CHAT_SEMANTIC_LOOKBACK_SECONDS},
    )
    if not isinstance(rawChat, dict) or not isinstance(rawChat.get("records"), list):
        raise RuntimeError("Raw chat capability returned an invalid snapshot.")

    interpretedChat = ctx.capabilities.call(
        "evilAnalysis.chatInterpret@1",
        {"records": rawChat["records"]},
    )
    if (
        not isinstance(interpretedChat, dict)
        or type(interpretedChat.get("text")) is not str
        or not isinstance(interpretedChat.get("records"), list)
    ):
        raise RuntimeError("Chat interpretation capability returned an invalid snapshot.")

    interpretedChat = dict(interpretedChat)
    interpretedChat["sourcePath"] = rawChat.get("sourcePath")
    return rawChat, interpretedChat


def _chatQueryItems(
    chat: dict[str, object],
    *,
    previous: dict[str, QueryItem],
) -> list[QueryItem]:
    """Materializes interpreted chat records without requiring source ingestion to parse semantics."""
    records = chat.get("records")
    if not isinstance(records, list):
        raise RuntimeError("Chat interpretation capability returned invalid records.")

    items: list[QueryItem] = []
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("Chat interpretation capability returned an invalid record.")
        analysis = record.get("analysis")
        if not isinstance(analysis, dict) or analysis.get("includedInText") is not True:
            continue

        lineNumber = record.get("lineNumber")
        rawMessage = record.get("message")
        username = record.get("username")
        body = record.get("body")
        streamTimeSeconds = analysis.get("streamTimeSeconds")
        streamTime = analysis.get("streamTime")
        if (
            type(lineNumber) is not int
            or type(rawMessage) is not str
            or type(streamTimeSeconds) not in {int, float}
            or type(streamTime) is not str
        ):
            raise RuntimeError("Chat interpretation returned invalid query-item evidence.")

        interpretationKind = analysis.get("kind")
        if interpretationKind == "unknownMessage":
            presentedAuthor = _UNCLASSIFIED_CHAT_AUTHOR
            content = rawMessage
        else:
            if type(username) is not str or not username or type(body) is not str:
                raise RuntimeError("Interpreted chat user/event record has invalid username/body evidence.")
            presentedAuthor = username
            content = body

        itemId = f"chat:{lineNumber}"
        previousItem = previous.get(itemId)
        if previousItem is not None:
            items.append(previousItem)
            continue

        items.append(
            QueryItem(
                itemId=itemId,
                kind="chat",
                content=content,
                metadata={
                    "streamStartSeconds": float(streamTimeSeconds),
                    "lineNumber": lineNumber,
                    "username": presentedAuthor,
                    "sourceUsername": username if type(username) is str else None,
                    "analysis": _plain(analysis),
                    "source": {
                        "sourcePath": chat.get("sourcePath"),
                        "timestampText": record.get("timestampText"),
                        "channel": record.get("channel"),
                        "rawMessage": rawMessage,
                    },
                },
            )
        )
    return items


def _buildQueryItems(ctx, payload):
    """Builds analysis QueryItems, explicitly separating raw chat selection from semantic interpretation."""
    if not isinstance(payload, dict) or not isinstance(payload.get("input"), dict):
        raise ValueError("BUILD_QUERY_ITEMS requires an input object.")
    inputValue = payload["input"]
    profile = inputValue.get("profile")
    window = inputValue.get("window")
    promptName = inputValue.get("promptName")
    if not isinstance(profile, dict) or not isinstance(window, dict) or type(promptName) is not str:
        raise ValueError("Processing input requires profile, window and promptName.")

    previous = _itemMap(payload)
    items: list[QueryItem] = []

    contextId = "context:evil-birthday"
    items.append(previous.get(contextId) or QueryItem(itemId=contextId, kind="context", content=_CONTEXT_TEXT))

    profileId = f"profile:{profile['name']}"
    profileText = f"Analysis profile: {profile['name']}\n{profile['description']}"
    items.append(
        previous.get(profileId)
        or QueryItem(itemId=profileId, kind="analysis-profile", content=profileText, metadata={"profile": _plain(profile)})
    )

    promptId = f"prompt:{promptName}"
    promptItem = previous.get(promptId)
    if promptItem is None:
        prompt = ctx.capabilities.call("evilAnalysis.prompts@1", {"name": promptName})
        if not isinstance(prompt, dict):
            raise RuntimeError("Prompt capability returned an invalid snapshot.")
        promptItem = QueryItem(
            itemId=promptId,
            kind="prompt",
            content=prompt["prompt"],
            metadata={"prompt": _plain(prompt)},
        )
    items.append(promptItem)

    for chunk in _windowChunkList(window):
        selector = {
            "videoStartSeconds": int(chunk["videoStartSeconds"]),
            "videoEndSeconds": int(chunk["videoEndSeconds"]),
        }
        transcript = ctx.capabilities.call("evilAnalysis.transcript@1", selector)
        if not isinstance(transcript, dict) or type(transcript.get("text")) is not str:
            raise RuntimeError("Transcript capability returned an invalid snapshot.")
        items.extend(_transcriptQueryItems(transcript, previous=previous))

        _rawChat, interpretedChat = _interpretedChat(ctx, selector)
        items.extend(_chatQueryItems(interpretedChat, previous=previous))
    return items


def _preparedChatChunk(ctx, chunk: dict[str, object]) -> dict[str, object]:
    """Creates saved chat evidence from the same raw->semantic boundary used for QueryItems."""
    selector = {
        "videoStartSeconds": int(chunk["videoStartSeconds"]),
        "videoEndSeconds": int(chunk["videoEndSeconds"]),
    }
    rawChat, interpretedChat = _interpretedChat(ctx, selector)

    text = interpretedChat["text"]
    interpretedRecords = interpretedChat["records"]
    rawRecords = rawChat["records"]

    sourceRecordCount = 0
    includedCount = 0
    suppressedCount = 0
    for rawRecord in rawRecords:
        if not isinstance(rawRecord, dict):
            raise RuntimeError("Raw chat capability returned an invalid record.")
        if rawRecord.get("insideRequestedWindow") is True:
            sourceRecordCount += 1

    for record in interpretedRecords:
        if not isinstance(record, dict):
            raise RuntimeError("Chat interpretation capability returned an invalid record.")
        if record.get("insideRequestedWindow") is not True:
            continue
        analysis = record.get("analysis")
        included = isinstance(analysis, dict) and analysis.get("includedInText") is True
        if included:
            includedCount += 1
        else:
            suppressedCount += 1

    metadataKeys = (
        "sourcePath",
        "chatStartTime",
        "streamStartTime",
        "streamStartVideoSeconds",
        "wallClockAtMediaZero",
        "wallClockAtStreamZero",
        "videoStartSeconds",
        "videoEndSeconds",
        "streamStartSeconds",
        "streamEndSeconds",
        "lookbackSeconds",
    )
    metadata = {key: _plain(rawChat[key]) for key in metadataKeys if key in rawChat}
    return {
        "offsetSeconds": int(chunk["offsetSeconds"]),
        "streamStartSeconds": int(chunk["streamStartSeconds"]),
        "streamEndSeconds": int(chunk["streamEndSeconds"]),
        "text": text,
        "metadata": metadata,
        "statistics": {
            "sourceRecordCount": sourceRecordCount,
            "includedRecordCount": includedCount,
            "suppressedRecordCount": suppressedCount,
            "renderedLineCount": 0 if not text else text.count("\n") + 1,
            "characterCount": len(text),
            "utf8ByteCount": len(text.encode("utf-8")),
        },
    }


def _budgetedChat(
    ctx,
    *,
    inputValue: dict[str, object],
    execution: object,
    byKind: dict[str, list[QueryItem]],
    allChatItems: list[QueryItem],
    chatLayout: str,
) -> tuple[list[QueryItem], dict[str, object], dict[str, int]]:
    """Selects a strict priority prefix of optional chat with logarithmic probes.

    Current-window chat remains mandatory. Older chat is ordered first by
    context offset (-10 before -30) and then by recency within that offset. The
    optional representation is therefore a strict priority prefix: once a
    higher-priority boundary cannot fit, lower-priority evidence is not allowed
    to leapfrog it merely because an individual message happens to be shorter.

    Exact model token measurements are used for the base query, mandatory
    current chat, the full optional request, and O(log N) priority-prefix probes
    when truncation is necessary. This avoids the previous O(N) sequence of
    complete prompt renders, identity sanitizations, and tokenizer requests.
    """

    window = inputValue.get("window")
    if not isinstance(window, dict):
        raise ValueError("BUILD_QUERY requires a processing window for chat budgeting.")

    maxInputTokens, reservedResponseTokens = _inputTokenBudget(execution)
    optionalFraction = _chatBudgetFraction(ctx.config)
    byOffset = _chatByOffset(window, allChatItems)
    currentItems = _orderedChat(byOffset.get(0, []))

    tokenMeasurementCount = 0

    baseText, identityStatistics = _renderPrompt(
        ctx,
        byKind=byKind,
        allChatItems=allChatItems,
        presentedChatItems=[],
        includeChat=True,
        chatLayout=chatLayout,
    )
    baseTokens = _measurePromptTokens(ctx, baseText)
    tokenMeasurementCount += 1

    mandatoryText, identityStatistics = _renderPrompt(
        ctx,
        byKind=byKind,
        allChatItems=allChatItems,
        presentedChatItems=currentItems,
        includeChat=True,
        chatLayout=chatLayout,
    )
    mandatoryTokens = _measurePromptTokens(ctx, mandatoryText)
    tokenMeasurementCount += 1
    if mandatoryTokens > maxInputTokens:
        position = window.get("positionSeconds")
        chunkSeconds = window.get("chunkSeconds")
        raise RuntimeError(
            "Required current chat window cannot fit the model input budget: "
            f"stream position {position!r}, chunkSeconds {chunkSeconds!r}, required query {mandatoryTokens} tokens, "
            f"available input {maxInputTokens} tokens after reserving {reservedResponseTokens} response tokens."
        )

    optionalOffsets = sorted((offset for offset in byOffset if offset != 0), reverse=True)
    optionalItems: list[QueryItem] = []
    optionalPriority: list[QueryItem] = []
    for offset in optionalOffsets:
        chunkItems = byOffset[offset]
        optionalItems.extend(chunkItems)
        optionalPriority.extend(
            sorted(chunkItems, key=lambda item: (_streamStart(item), _chatLineNumber(item)), reverse=True)
        )

    availableChatTokens = max(0, maxInputTokens - baseTokens)
    fractionLimit = int(availableChatTokens * optionalFraction)
    absoluteRemaining = max(0, maxInputTokens - mandatoryTokens)
    optionalTokenLimit = min(fractionLimit, absoluteRemaining)

    def fits(tokens: int) -> bool:
        optionalCost = max(0, tokens - mandatoryTokens)
        return tokens <= maxInputTokens and optionalCost <= optionalTokenLimit

    prefixTokens: dict[int, int] = {0: mandatoryTokens}

    def measurePrefix(count: int) -> int:
        nonlocal tokenMeasurementCount
        cached = prefixTokens.get(count)
        if cached is not None:
            return cached
        candidateText, _ = _renderPrompt(
            ctx,
            byKind=byKind,
            allChatItems=allChatItems,
            presentedChatItems=[*currentItems, *optionalPriority[:count]],
            includeChat=True,
            chatLayout=chatLayout,
        )
        measured = _measurePromptTokens(ctx, candidateText)
        tokenMeasurementCount += 1
        prefixTokens[count] = measured
        return measured

    optionalCount = len(optionalPriority)
    if optionalCount:
        fullTokens = measurePrefix(optionalCount)
    else:
        fullTokens = mandatoryTokens

    if optionalCount == 0 or fits(fullTokens):
        selectedCount = optionalCount
    elif optionalTokenLimit <= 0:
        selectedCount = 0
    else:
        low = 0
        high = optionalCount - 1
        while low < high:
            middle = (low + high + 1) // 2
            if fits(measurePrefix(middle)):
                low = middle
            else:
                high = middle - 1
        selectedCount = low

    selectedOptional = optionalPriority[:selectedCount]
    finalTokens = measurePrefix(selectedCount)

    selectedOptionalIds = {item.itemId for item in selectedOptional}
    selectedByOffset = {
        str(offset): sum(1 for item in byOffset[offset] if item.itemId in selectedOptionalIds)
        for offset in optionalOffsets
    }
    requestedByOffset = {str(offset): len(byOffset[offset]) for offset in optionalOffsets}
    optionalTruncated = selectedCount != optionalCount
    warnings: list[str] = []
    if optionalTruncated:
        warnings.append(
            "Older chat context exceeded its optional token allowance; lower-priority messages were omitted from the prompt."
        )

    evidence = {
        "maxInputTokens": maxInputTokens,
        "reservedResponseTokens": reservedResponseTokens,
        "baseQueryTokens": baseTokens,
        "requiredCurrentChatTokens": max(0, mandatoryTokens - baseTokens),
        "requiredQueryTokens": mandatoryTokens,
        "availableChatTokens": availableChatTokens,
        "optionalContextMaxFraction": optionalFraction,
        "optionalTokenLimit": optionalTokenLimit,
        "optionalRequestedTokens": max(0, fullTokens - mandatoryTokens),
        "optionalIncludedTokens": max(0, finalTokens - mandatoryTokens),
        "optionalRequestedItemCount": optionalCount,
        "optionalIncludedItemCount": selectedCount,
        "optionalRequestedByOffset": requestedByOffset,
        "optionalIncludedByOffset": selectedByOffset,
        "optionalTruncated": optionalTruncated,
        "tokenMeasurementCount": tokenMeasurementCount,
        "warnings": warnings,
    }
    return [*currentItems, *selectedOptional], evidence, identityStatistics


# Functions defined in the implementation module resolve globals in that module.
# Patch the boundary adapters there so every registered analysis capability uses
# the raw-chat -> semantic-interpretation path and optimized chat selector.
_implementation._chatQueryItems = _chatQueryItems
_implementation._buildQueryItems = _buildQueryItems
_implementation._preparedChatChunk = _preparedChatChunk
_implementation._budgetedChat = _budgetedChat


def onLoad(ctx):
    _implementation.onLoad(ctx)
