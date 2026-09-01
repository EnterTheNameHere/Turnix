# file: first-party/applications/evilBirthdayAnalysis/packs/results/codeEntry.py ; version: 1
from __future__ import annotations

from pathlib import Path


def _save(ctx, payload):
    if not isinstance(payload, dict) or type(payload.get("resultId")) is not str:
        raise ValueError("Result persistence requires a result record with resultId.")
    output = ctx.config.get("outputDirectory")
    if type(output) is not str:
        raise ValueError("Application config outputDirectory must be a string path.")
    path = Path(output) / f"{payload['resultId']}.json"
    ctx.io.writeJsonAtomic(path, payload)
    return {"resultId": payload["resultId"], "path": str(path.resolve())}


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.results@1", _save)
