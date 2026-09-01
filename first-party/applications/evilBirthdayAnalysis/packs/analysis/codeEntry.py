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
    if type(start) is not str or type(end) is not str:
        raise ValueError("analysisBatch.start/end must be HH:MM:SS strings.")
    if type(step) is not int or step <= 0:
        raise ValueError("analysisBatch.stepSeconds must be a positive integer.")
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
    }


def _contextGeometry(settings: dict[str, object]) -> tuple[int, tuple[int, ...]]:
    chunkSeconds = settings.get("chunkSeconds", 600)
    offsets = settings.get("contextOffsetsSeconds", [0, -600, -1800])
    if type(chunkSeconds) is not int or chunkSeconds <= 0:
        raise ValueError("Profile setting 'chunkSeconds' must be a positive exact integer number of seconds.")
    if not isinstance(offsets, list) or not offsets or any(type(offset) is not int for offset in offsets):
        raise TypeError("Profile setting 'contextOffsetsSeconds' must be a non-empty list of exact integer offsets.")
    if len(set(offsets)) != len(offsets):
        raise ValueError("Profile setting 'contextOffsetsSeconds' must not contain duplicate offsets.")
    if 0 not in offsets:
        raise ValueError("Profile setting 'contextOffsetsSeconds' must contain the current offset 0.")
    return chunkSeconds, tuple(offsets)


def _streamStartVideoSeconds(config: dict[str, object]) -> int:
    streamStartTime = config.get("streamStartTime")
    if type(streamStartTime) is not str:
        raise ValueError("streamStartTime must be configured as HH:MM:SS.")
    return _timeSeconds(streamStartTime, fieldName="streamStartTime")


def _windowChunks(
    *,
    positionSeconds: int,
    chunkSeconds: int,
    offsetsSeconds: tuple[int, ...],
    streamStartVideoSeconds: int,
) -> list[dict[str, int]]:
    chunks: list[dict[str, int]] = []
    for offsetSeconds in offsetsSeconds:
        streamStartSeconds = positionSeconds + offsetSeconds
        streamEndSeconds = streamStartSeconds + chunkSeconds
        chunks.append(
            {
                "offsetSeconds": offsetSeconds,
                "streamStartSeconds": streamStartSeconds,
                "streamEndSeconds": streamEndSeconds,
                "videoStartSeconds": streamStartSeconds + streamStartVideoSeconds,
                "videoEndSeconds": streamEndSeconds + streamStartVideoSeconds,
            }
        )
    return chunks


def _itemMap(payload: dict[str, object]) -> dict[str, QueryItem]:
    previous = payload.get("previousQueryItems", [])
    if not isinstance(previous, list):
        raise TypeError("previousQueryItems must be a list.")
    result: dict[str, QueryItem] = {}
    for snapshot in previous:
        item = QueryItem.fromSnapshot(snapshot)
        result[item.itemId] = item
    return result


def _windowChunkList(window: dict[str, object]) -> list[dict[str, object]]:
    chunks = window.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise ValueError("Processing window requires a non-empty chunks list.")
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise TypeError("Processing window chunks must be objects.")
        for key in ("offsetSeconds", "streamStartSeconds", "streamEndSeconds", "videoStartSeconds", "videoEndSeconds"):
            if type(chunk.get(key)) is not int:
                raise TypeError(f"Processing window chunk {key!r} must be an exact integer second offset.")
    return chunks


def _buildQueryItems(ctx, payload):
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
        streamStart = int(chunk["streamStartSeconds"])
        streamEnd = int(chunk["streamEndSeconds"])
        transcriptId = f"transcript:{streamStart}-{streamEnd}"
        transcriptItem = previous.get(transcriptId)
        if transcriptItem is None:
            transcript = ctx.capabilities.call(
                "evilAnalysis.transcript@1",
                {
                    "videoStartSeconds": int(chunk["videoStartSeconds"]),
                    "videoEndSeconds": int(chunk["videoEndSeconds"]),
                },
            )
            if not isinstance(transcript, dict) or type(transcript.get("text")) is not str:
                raise RuntimeError("Transcript capability returned an invalid snapshot.")
            if not transcript["text"]:
                continue
            transcriptItem = QueryItem(
                itemId=transcriptId,
                kind="transcript",
                content=transcript["text"],
                metadata={
                    "offsetSeconds": int(chunk["offsetSeconds"]),
                    "source": _plain(transcript),
                },
            )
        items.append(transcriptItem)
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

    inputValue = payload["input"]
    return {
        "formatId": "text/plain",
        "payload": "\n\n".join(sections),
        "metadata": {
            "profileName": inputValue["profile"]["name"],
            "promptName": inputValue["promptName"],
            "windowIndex": inputValue["windowIndex"],
            "streamPositionSeconds": inputValue["window"]["positionSeconds"],
            "chatIncluded": False,
        },
    }


def _preparedChatChunk(ctx, chunk: dict[str, object]) -> dict[str, object]:
    startVideo = int(chunk["videoStartSeconds"])
    endVideo = int(chunk["videoEndSeconds"])
    chat = ctx.capabilities.call(
        "evilAnalysis.chat@1",
        {"videoStartSeconds": startVideo, "videoEndSeconds": endVideo},
    )
    if not isinstance(chat, dict) or type(chat.get("text")) is not str or not isinstance(chat.get("records"), list):
        raise RuntimeError("Chat capability returned an invalid snapshot.")

    text = chat["text"]
    records = chat["records"]
    includedCount = 0
    suppressedCount = 0
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("Chat capability returned an invalid record.")
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
        "startWallClock",
        "endWallClock",
    )
    metadata = {key: _plain(chat[key]) for key in metadataKeys if key in chat}
    return {
        "offsetSeconds": int(chunk["offsetSeconds"]),
        "streamStartSeconds": int(chunk["streamStartSeconds"]),
        "streamEndSeconds": int(chunk["streamEndSeconds"]),
        "text": text,
        "metadata": metadata,
        "statistics": {
            "sourceRecordCount": len(records),
            "includedRecordCount": includedCount,
            "suppressedRecordCount": suppressedCount,
            "renderedLineCount": 0 if not text else text.count("\n") + 1,
            "characterCount": len(text),
            "utf8ByteCount": len(text.encode("utf-8")),
        },
    }


def _preparedChatSnapshot(ctx, window: dict[str, object]) -> dict[str, object]:
    chunks = [_preparedChatChunk(ctx, chunk) for chunk in _windowChunkList(window)]
    statisticKeys = (
        "sourceRecordCount",
        "includedRecordCount",
        "suppressedRecordCount",
        "renderedLineCount",
        "characterCount",
        "utf8ByteCount",
    )
    totals = {
        key: sum(int(chunk["statistics"][key]) for chunk in chunks)
        for key in statisticKeys
    }
    return {
        "prepared": True,
        "includedInPrompt": False,
        "chunks": chunks,
        "statistics": totals,
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

    finalizeInput = payload.get("finalizeInput")
    if not isinstance(finalizeInput, dict) or not isinstance(finalizeInput.get("chat"), dict):
        raise ValueError("FINALIZE requires prepared chat evidence.")
    preparedChat = finalizeInput["chat"]

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
        "chat": _plain(preparedChat),
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
    chunkSeconds, offsetsSeconds = _contextGeometry(settings)
    streamStartVideoSeconds = _streamStartVideoSeconds(ctx.config)

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
        chunks = _windowChunks(
            positionSeconds=position,
            chunkSeconds=chunkSeconds,
            offsetsSeconds=offsetsSeconds,
            streamStartVideoSeconds=streamStartVideoSeconds,
        )
        window = {
            "positionSeconds": position,
            "chunkSeconds": chunkSeconds,
            "contextOffsetsSeconds": list(offsetsSeconds),
            "streamStartVideoSeconds": streamStartVideoSeconds,
            "chunks": chunks,
            "chatPrepared": True,
            "chatIncluded": False,
        }
        inputValue = {
            "batchId": batchId,
            "windowIndex": windowIndex,
            "profile": profile,
            "promptName": promptName,
            "window": window,
        }
        preparedChat = _preparedChatSnapshot(ctx, window)
        processingResult = ctx.llm.runProcessing(
            memoryKey="evilbirthday",
            inputValue=inputValue,
            buildQueryItemsCapabilityId="evilAnalysis.buildQueryItems@1",
            buildQueryCapabilityId="evilAnalysis.buildQuery@1",
            finalizeCapabilityId="evilAnalysis.finalizeWindow@1",
            finalizeInput={"chat": preparedChat},
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
