# file: first-party/bootstrapExtra/activation.py
from __future__ import annotations


def onLoad(ctx):
    def reverse(payload):
        if not isinstance(payload, dict):
            raise ValueError(f"reverse payload must be a dict, not {type(payload)}.")

        text = payload.get("text")
        if not isinstance(text, str):
            raise ValueError(f"reverse payload must contain a 'text' key with a string value, got {type(text)}.")

        return {"text": text[::-1]}

    ctx.registerCapability("bootstrapExtra.reverse@1", reverse)
