# file: first-party/bootstrap/activation.py
from __future__ import annotations


def onLoad(ctx):
    def echo(payload):
        if not isinstance(payload, dict):
            raise ValueError(f"echo() expects a dict payload, got {type(payload)}.")

        text = payload.get("text")
        if not isinstance(text, str):
            raise ValueError(f"echo() expects a 'text' string in payload, got {type(text)}.")

        return {"text": text}

    ctx.registerCapability("bootstrap.activationEcho@1", echo)
