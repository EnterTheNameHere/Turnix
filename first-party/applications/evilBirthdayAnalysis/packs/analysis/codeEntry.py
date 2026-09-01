# file: first-party/applications/evilBirthdayAnalysis/packs/analysis/codeEntry.py ; version: 4
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from backend.core.runtimeIds import newRuntimeId
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

    # Resolve mutable application configuration once at task preparation time.
    # Everything below uses these snapshots so an edited source/config file
    # cannot silently change one already-started analysis task halfway through.
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
    model = llmConfig.get("model")
    if model is not None and type(model) is not str:
        raise ValueError("llm.model must be null or a string.")

    observer = request.get("streamObserver")
    if observer is not None and not callable(observer):
        raise TypeError("streamObserver must be callable when supplied.")

    query = LlmQuery(
        formatId="text/plain",
        payload=finalInput,
        metadata={"profileName": profile["name"], "promptName": prompt["name"]},
    )
    llmResult = ctx.llm.run(
        providerName=llmConfig["provider"],
        query=query,
        model=model,
        providerOptions=providerOptions,
        streamObserver=observer,
    )

    resultId = newRuntimeId()
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
            "providerOwnerId": llmResult.providerOwnerId,
            "model": llmResult.model,
            "requestedProviderOptions": _plain(llmResult.providerOptions),
            "executionProfile": {
                "contextWindowTokens": llmResult.executionProfile.contextWindowTokens,
                "metadata": _plain(llmResult.executionProfile.metadata),
            },
            "providerMetadata": _plain(llmResult.providerMetadata),
        },
        "llamaCpp": _plain(ctx.config.get("llamaCpp", {})),
        "input": {
            "formatId": llmResult.query.formatId,
            "exactPayload": llmResult.query.payload,
            "metadata": _plain(llmResult.query.metadata),
        },
        "response": {"rawText": llmResult.rawText},
    }
    saved = ctx.capabilities.call("evilAnalysis.results@1", record)
    return {"result": record, "saved": saved}


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.run@1", _run)
