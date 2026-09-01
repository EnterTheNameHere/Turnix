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
# us optimize one hot path without rewriting the large source module in place.
for _name, _value in vars(_implementation).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


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


# _buildQuery was defined in the implementation module and resolves globals in
# that module. Replacing the implementation's hot-path function therefore makes
# every registered analysis capability use the optimized selector as well.
_implementation._budgetedChat = _budgetedChat


def onLoad(ctx):
    _implementation.onLoad(ctx)
