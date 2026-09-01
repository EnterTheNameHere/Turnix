# file: first-party/applications/evilBirthdayAnalysis/packs/results/codeEntry.py ; version: 2
from __future__ import annotations

from pathlib import Path


def _save(ctx, payload):
    if not isinstance(payload, dict) or type(payload.get("resultId")) is not str or not payload["resultId"]:
        raise ValueError("Result persistence requires a result record with a non-empty resultId.")
    output = ctx.config.get("outputDirectory")
    if type(output) is not str or not output.strip():
        raise ValueError("Application config outputDirectory must be a non-blank string path.")
    path = Path(output) / f"{payload['resultId']}.json"
    ctx.io.writeJsonAtomic(path, payload)
    # ManagedIo owns physical path resolution and confines it to the Pack root.
    # Report the same managed path contract instead of resolving against the
    # process CWD, which may identify a different file entirely.
    return {"resultId": payload["resultId"], "path": str(path)}


def onLoad(ctx):
    ctx.capabilities.register("evilAnalysis.results@1", _save)
