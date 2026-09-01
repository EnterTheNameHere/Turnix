# file: first-party/applications/evilBirthdayAnalysis/packs/prompts/codeEntry.py ; version: 1
from __future__ import annotations


def _select(ctx, payload):
    if not isinstance(payload, dict) or type(payload.get("name")) is not str:
        raise ValueError("Prompt selection requires {'name': <prompt name>}.")
    promptPath = ctx.config.get("promptsFile")
    if type(promptPath) is not str:
        raise ValueError("Application config promptsFile must be a string path.")
    definitions = ctx.io.readJson(promptPath)
    try:
        definition = definitions[payload["name"]]
    except KeyError as err:
        raise LookupError(f"Prompt is not defined: {payload['name']}.") from err
    if not isinstance(definition, dict):
        raise ValueError(f"Prompt definition {payload['name']!r} must be an object.")
    description = definition.get("description")
    prompt = definition.get("prompt")
    if type(description) is not str or type(prompt) is not str or not prompt.strip():
        raise ValueError(f"Prompt definition {payload['name']!r} requires string description and non-blank prompt.")
    return {"name": payload["name"], "description": description, "prompt": prompt}


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.prompts@1", _select)
