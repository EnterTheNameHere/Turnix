# backend/ui/notifications.py
from __future__ import annotations
from fastapi import WebSocket
from typing import Literal, Mapping, Any

from backend.rpc.transport import sendRPCMessage
from backend.rpc.models import RPCMessage, Route, Gen
from backend.core.ids import uuidv7

__all__ = ["pushToast"]

ToastLevel = Literal["info", "warn", "error", "success"]

def _normalizeLevel(level: str) -> ToastLevel:
    lvl = (level or "").strip().lower()
    if lvl == "warning":
        lvl = "warn"
    return lvl if lvl in ("info", "warn", "error", "success") else "info" # Safe fallback



def _asGen(gen: Gen | Mapping[str, Any]) -> Gen:
    return gen if isinstance(gen, Gen) else Gen.model_validate(gen)



async def pushToast(
        ws: WebSocket,
        *,
        gen: Gen | Mapping[str, Any],
        level: ToastLevel,
        text: str,
        ttlMs: int = 5000) -> None:
    lvl = _normalizeLevel(level)
    ttl = max(100, min(int(ttlMs), 60_000))
    await sendRPCMessage(ws, RPCMessage(
        id=uuidv7(),
        v="0.1",
        type="emit",
        budgetMs=ttl,
        route=Route(capability="ui.toast@1"),
        gen=_asGen(gen),
        payload={"level": lvl, "text": text},
    ))
