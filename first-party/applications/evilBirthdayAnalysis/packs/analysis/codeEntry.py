from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from backend.core.runtimeIds import newRuntimeId
from backend.processing.runtime import QueryItem

_CONTEXT_TEXT = (
    'Context: "Evil," also known as "Evil Neuro," is the name of the AI VTuber character being analyzed. '
    'She is presented as Neuro-sama\'s "evil" sister. This material comes from Evil\'s birthday stream.'
)


def _plain(value):
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _timeSeconds(value: str, *, fieldName: str) -> int:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"{fieldName} must use HH:MM:SS.")
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError as err:
        raise ValueError(f"{fieldName} must use HH:MM:SS.") from err
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError(f"{fieldName} must use HH:MM:SS.")
    return hours * 3600 + minutes * 60 + seconds


def _nonNegativeIntegerSeconds(settings: dict[str, object], key: str, default: int) -> int:
    value = settings.get(key, default)
    if type(value) is not int:
        raise TypeError(f"Profile setting {key!r} must be an exact integer number of seconds.")
    if value < 0:
        raise ValueError(f"Profile setting {key!r} must be non-negative.")
    return value


def _profileSnapshot(config: dict[str, object]) -> dict[str, object]:
    name = config.get("activeProfile")
    profiles = config.get("profiles")
    if type(name) is not str or not isinstance(profiles, dict):
        raise ValueError("activeProfile and profiles must be configured.")
    try:
        definition = profiles[name]
    except KeyError as err:
        raise LookupError(f"Active profile is not defined: {name}.") from err
    if not isinstance(definition, dict) or type(definition.get("description")) is not str:
        raise ValueError(f"Profile {name!r} requires a description.")
    settings = {key: _plain(value) for key, value in definition.items() if key != "description"}
    return {"name": name, "description": definition["description"], "settings": settings}


def _batchSnapshot(config: dict[str, object]) -> dict[str, object]:
    definition = config.get("analysisBatch")
    if not isinstance(definition, dict):
        raise ValueError("analysisBatch must be configured.")
    start = definition.get("start")
    end = definition.get("end")
    step = definition.get("stepSeconds", 600)
    chunk = definition.get("chatChunkSeconds", step)
    if type(start) is not str or type(end) is not str:
        raise ValueError("analysisBatch.start/end must be HH:MM:SS strings.")
    if type(step) is not int or step <= 0 or type(chunk) is not int or chunk <= 0:
        raise ValueError("analysisBatch stepSeconds/chatChunkSeconds must be positive integers.")
    startSeconds = _timeSeconds(start, fieldName="analysisBatch.start")
    endSeconds = _timeSeconds(end, fieldName="analysisBatch.end")
    if endSeconds <= startSeconds:
        raise ValueError("analysisBatch.end must be later than analysisBatch.start.")
    return {
        "start": start,
        "end": end,
        "startSeconds": startSeconds,
        "endSeconds": endSeconds,
        "stepSeconds": step,
        "chatChunkSeconds": chunk,
    }


def _itemMap(payload: dict[str, object]) -> dict[str, QueryItem]:
    previous = payload.get("previousQueryItems", [])
    if not isinstance(previous, list):
        raise TypeError("previousQueryItems must be a list.")
    result: dict[str, QueryItem] = {}
    for snapshot in previous:
        item = QueryItem.fromSnapshot(snapshot)
        result[item.itemId] = item
    return result


def _requireWindowSecond(window: dict[str, object], key: str) -> int:
    value = window.get(key)
    if type(value) is not int:
        raise TypeError(f"Processing window {key!r} must be an exact integer second offset.")
    return value


def _buildQueryItems(ctx, payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("input"), dict):
        raise ValueError("BUILD_QUERY_ITEMS requires an input object.")
    inputValue = payload["input"]
    profile = inputValue.get("profile")
    window = inputValue.get("window")
    promptName = inputValue.get("promptName")
    chatChunkSeconds = inputValue.get("chatChunkSeconds")
    if not isinstance(profile, dict) or not isinstance(window, dict) or type(promptName) is not str:
        raise ValueError("Processing input requires profile, window and promptName.")
    if type(chatChunkSeconds) is not int or chatChunkSeconds <= 0:
        raise ValueError("Processing input chatChunkSeconds must be a positive integer.")

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

    startVideo = _requireWindowSecond(window, "transcriptStartSeconds")
    endVideo = _requireWindowSecond(window, "transcriptEndSeconds")
    transcriptId = f"transcript:{startVideo}-{endVideo}"
    transcript = ctx.capabilities.call(
        "evilAnalysis.transcript@1",
        {"videoStartSeconds": startVideo, "videoEndSeconds": endVideo},
    )
    if not isinstance(transcript, dict):
        raise RuntimeError("Transcript capability returned an invalid snapshot.")
    items.append(
        QueryItem(
            itemId=transcriptId,
            kind="transcript",
            content=transcript["text"],
            metadata={"source": _plain(transcript)},
        )
    )

    chatStart = _requireWindowSecond(window, "chatStartSeconds")
    chatEnd = _requireWindowSecond(window, "chatEndSeconds")
    for chunkStart in range(chatStart, chatEnd, chatChunkSeconds):
        chunkEnd = min(chunkStart + chatChunkSeconds, chatEnd)
        itemId = f"chat:{chunkStart}-{chunkEnd}"
        cached = previous.get(itemId)
        if cached is not None:
            items.append(cached)
            continue
        chat = ctx.capabilities.call(
            "evilAnalysis.chat@1",
            {"videoStartSeconds": chunkStart, "videoEndSeconds": chunkEnd},
        )
        if not isinstance(chat, dict):
            raise RuntimeError("Chat capability returned an invalid snapshot.")
        items.append(
            QueryItem(
                itemId=itemId,
                kind="chat",
                content=chat["text"],
                metadata={"source": _plain(chat), "videoStartSeconds": chunkStart, "videoEndSeconds": chunkEnd},
            )
        )
    return items


def _buildQuery(_ctx, payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("input"), dict):
        raise ValueError("BUILD_QUERY requires an input object.")
    snapshots = payload.get("queryItems")
    if not isinstance(snapshots, list):
        raise ValueError("BUILD_QUERY requires queryItems.")
    items = [QueryItem.fromSnapshot(snapshot) for snapshot in snapshots]
    byKind: dict[str, list[QueryItem]] = {}
    for item in items:
        byKind.setdefault(item.kind, []).append(item)

    sections: list[str] = []
    for kind, heading in (
        ("context", "CONTEXT"),
        ("analysis-profile", "ANALYSIS PROFILE"),
        ("prompt", "ANALYSIS INSTRUCTION"),
        ("transcript", "TRANSCRIPT WINDOW"),
    ):
        for item in byKind.get(kind, []):
            sections.append(f"{heading}\n{item.content}")

    chatItems = sorted(
        byKind.get("chat", []),
        key=lambda item: int(item.metadata.get("videoStartSeconds", 0)),
    )
    if chatItems:
        sections.append("CHAT WINDOW\n" + "\n".join(item.content for item in chatItems if item.content))

    inputValue = payload["input"]
    return {
        "formatId": "text/plain",
        "payload": "\n\n".join(sections),
        "metadata": {
            "profileName": inputValue["profile"]["name"],
            "promptName": inputValue["promptName"],
            "windowIndex": inputValue["windowIndex"],
            "videoStartSeconds": inputValue["window"]["transcriptStartSeconds"],
        },
    }


def _finalizeWindow(ctx, payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("input"), dict) or not isinstance(payload.get("llm"), dict):
        raise ValueError("FINALIZE requires processing input and LLM evidence.")
    inputValue = payload["input"]
    llm = payload["llm"]
    query = llm.get("query")
    response = llm.get("response")
    if not isinstance(query, dict) or not isinstance(response, dict):
        raise ValueError("FINALIZE requires query and response evidence.")
    exactPayload = query.get("payload")
    if type(exactPayload) is not str:
        raise TypeError("Evil Birthday analysis finalization expects a text/plain string query payload.")

    batchId = inputValue.get("batchId")
    if type(batchId) is not str:
        raise ValueError("Processing input batchId must be a string.")

    record = {
        "resultId": newRuntimeId(),
        "batchId": batchId,
        "processingRunId": payload["processingRunId"],
        "createdAt": datetime.now(UTC).isoformat(),
        "application": {
            "applicationId": ctx.identity.applicationId,
            "applicationRunId": ctx.identity.applicationRunId,
        },
        "profile": _plain(inputValue["profile"]),
        "window": _plain(inputValue["window"]),
        "queryItems": _plain(payload["queryItems"]),
        "llm": {
            "provider": llm.get("providerName"),
            "providerOwnerId": llm.get("providerOwnerId"),
            "model": llm.get("model"),
            "requestedProviderOptions": _plain(llm.get("providerOptions", {})),
            "executionProfile": _plain(llm.get("executionProfile", {})),
            "providerMetadata": _plain(llm.get("providerMetadata", {})),
            "observerErrors": _plain(llm.get("observerErrors", [])),
        },
        "llamaCpp": _plain(ctx.config.get("llamaCpp", {})),
        "input": {
            "formatId": query.get("formatId"),
            "exactPayload": exactPayload,
            "metadata": _plain(query.get("metadata", {})),
        },
        "response": {"rawText": response.get("rawText", "")},
    }
    saved = ctx.capabilities.call("evilAnalysis.results@1", record)
    return {"result": record, "saved": saved}


def _run(ctx, payload):
    request = {} if payload is None else payload
    if not isinstance(request, dict):
        raise ValueError("Analysis request must be an object.")

    profile = _profileSnapshot(ctx.config)
    batch = _batchSnapshot(ctx.config)
    promptName = ctx.config.get("activePrompt")
    if type(promptName) is not str:
        raise ValueError("activePrompt must be configured.")

    settings = profile["settings"]
    if not isinstance(settings, dict):
        raise RuntimeError("Profile snapshot has invalid settings.")
    transcriptBefore = _nonNegativeIntegerSeconds(settings, "transcriptBeforeSeconds", 0)
    transcriptAfter = _nonNegativeIntegerSeconds(settings, "transcriptAfterSeconds", 600)
    chatBefore = _nonNegativeIntegerSeconds(settings, "chatBeforeSeconds", 1800)
    chatAfter = _nonNegativeIntegerSeconds(settings, "chatAfterSeconds", 1800)

    llmConfig = ctx.config.get("llm")
    if not isinstance(llmConfig, dict) or type(llmConfig.get("provider")) is not str:
        raise ValueError("llm.provider must be configured.")
    providerOptions = llmConfig.get("providerOptions", {})
    if not isinstance(providerOptions, dict):
        raise ValueError("llm.providerOptions must be an object.")
    model = llmConfig.get("model")
    if model is not None and type(model) is not str:
        raise ValueError("llm.model must be null or a string.")

    observer = request.get("streamObserver")
    if observer is not None and not callable(observer):
        raise TypeError("streamObserver must be callable when supplied.")

    batchId = newRuntimeId()
    results: list[dict[str, object]] = []
    positions = range(batch["startSeconds"], batch["endSeconds"], batch["stepSeconds"])
    for windowIndex, position in enumerate(positions):
        transcriptStart = position - transcriptBefore
        transcriptEnd = position + transcriptAfter
        inputValue = {
            "batchId": batchId,
            "windowIndex": windowIndex,
            "profile": profile,
            "promptName": promptName,
            "chatChunkSeconds": batch["chatChunkSeconds"],
            "window": {
                "positionSeconds": position,
                "transcriptStartSeconds": transcriptStart,
                "transcriptEndSeconds": transcriptEnd,
                "chatStartSeconds": transcriptStart - chatBefore,
                "chatEndSeconds": transcriptEnd + chatAfter,
            },
        }
        processingResult = ctx.llm.runProcessing(
            memoryKey="evilbirthday",
            inputValue=inputValue,
            buildQueryItemsCapabilityId="evilAnalysis.buildQueryItems@1",
            buildQueryCapabilityId="evilAnalysis.buildQuery@1",
            finalizeCapabilityId="evilAnalysis.finalizeWindow@1",
            providerName=llmConfig["provider"],
            model=model,
            providerOptions=providerOptions,
            streamObserver=observer,
        )
        finalized = processingResult.finalizeResult
        if not isinstance(finalized, dict) or not isinstance(finalized.get("result"), dict) or not isinstance(finalized.get("saved"), dict):
            raise RuntimeError("Window finalization returned an invalid result.")
        results.append(
            {
                "windowIndex": windowIndex,
                "positionSeconds": position,
                "processingRunId": processingResult.processingRunId,
                "result": finalized["result"],
                "saved": finalized["saved"],
            }
        )

    return {"batchId": batchId, "batch": batch, "profile": profile, "results": results}


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.buildQueryItems@1", _buildQueryItems)
    ctx.capabilities.register("evilAnalysis.buildQuery@1", _buildQuery)
    ctx.capabilities.register("evilAnalysis.finalizeWindow@1", _finalizeWindow)
    ctx.capabilities.register("evilAnalysis.run@1", _run)
