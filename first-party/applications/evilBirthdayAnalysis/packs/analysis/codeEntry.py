# file: first-party/applications/evilBirthdayAnalysis/packs/analysis/codeEntry.py ; version: 2
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from backend.core.ids import uuidv4hex
from backend.llm.llmTypes import LlmQuery


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


def _composeInput(prompt: dict[str, object], profile: dict[str, object], transcript: dict[str, object], chat: dict[str, object]) -> str:
    return (
        f"{prompt['prompt']}\n\n"
        f"ANALYSIS PROFILE\nName: {profile['name']}\nDescription: {profile['description']}\n\n"
        f"TRANSCRIPT WINDOW\n{transcript['text']}\n\n"
        f"CHAT WINDOW\n{chat['text']}"
    )


def _run(ctx, payload):
    request = {} if payload is None else payload
    if not isinstance(request, dict):
        raise ValueError("Analysis request must be an object.")
    profile = _profileSnapshot(ctx.config)
    promptName = ctx.config.get("activePrompt")
    if type(promptName) is not str:
        raise ValueError("activePrompt must be configured.")
    prompt = ctx.capabilities.call("evilAnalysis.prompts@1", {"name": promptName})
    transcript = ctx.capabilities.call("evilAnalysis.transcript@1", {"profile": profile})
    chat = ctx.capabilities.call("evilAnalysis.chat@1", {"profile": profile, "transcript": transcript})
    if not all(isinstance(item, dict) for item in (prompt, transcript, chat)):
        raise RuntimeError("Analysis source capabilities returned invalid snapshots.")
    finalInput = _composeInput(prompt, profile, transcript, chat)
    llmConfig = ctx.config.get("llm")
    if not isinstance(llmConfig, dict) or type(llmConfig.get("provider")) is not str:
        raise ValueError("llm.provider must be configured.")
    providerOptions = llmConfig.get("providerOptions", {})
    if not isinstance(providerOptions, dict):
        raise ValueError("llm.providerOptions must be an object.")
    observer = request.get("streamObserver")
    if observer is not None and not callable(observer):
        raise TypeError("streamObserver must be callable when supplied.")
    llmResult = ctx.llm.run(
        providerName=llmConfig["provider"],
        query=LlmQuery(formatId="text/plain", payload=finalInput, metadata={"profileName": profile["name"], "promptName": prompt["name"]}),
        model=llmConfig.get("model"),
        providerOptions=providerOptions,
        streamObserver=observer,
    )
    resultId = uuidv4hex()
    record = {
        "resultId": resultId,
        "createdAt": datetime.now(UTC).isoformat(),
        "application": {
            "applicationId": ctx.identity.applicationId,
            "applicationRunId": ctx.identity.applicationRunId,
        },
        "profile": _plain(profile),
        "prompt": _plain(prompt),
        "sources": {"transcript": _plain(transcript), "chat": _plain(chat)},
        "llm": {
            "provider": llmResult.providerName,
            "model": llmResult.model,
            "requestedProviderOptions": _plain(llmResult.providerOptions),
            "providerMetadata": _plain(llmResult.providerMetadata),
        },
        "llamaCpp": _plain(ctx.config.get("llamaCpp", {})),
        "input": {
            "formatId": llmResult.query.formatId,
            "exactPayload": finalInput,
            "metadata": _plain(llmResult.query.metadata),
        },
        "response": {"rawText": llmResult.rawText},
    }
    saved = ctx.capabilities.call("evilAnalysis.results@1", record)
    return {"result": record, "saved": saved}


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.run@1", _run)
