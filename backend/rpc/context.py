# backend/rpc/context.py
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["CallContext"]



@dataclass(slots=True)
class CallContext:
    id: str
    origin: dict[str, Any] | None

EmitContext = CallContext # Alias, identical interface



@dataclass(slots=True)
class SubscribeContext:
    id: str
    origin: dict[str, Any] | None
    signal: asyncio.Event
    _push: Callable[[dict[str, Any]], None]
    
    def push(self, payload: dict[str, Any]) -> None:
        try:
            self._push(payload)
        except Exception:
            # Never blow up router
            pass
