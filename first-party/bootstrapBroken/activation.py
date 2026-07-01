# file: first-party/bootstrapBroken/activation.py
from __future__ import annotations


def onLoad(ctx):
    raise RuntimeError("Intentional activation failure.")
