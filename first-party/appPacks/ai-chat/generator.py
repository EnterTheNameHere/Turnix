# first-party/appPacks/ai-chat/generator.py
from __future__ import annotations
from collections.abc import Mapping
from typing import Any

def generate(context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raise NotImplementedError()
