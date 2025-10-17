# backend/handlers/context.py
from __future__ import annotations
from dataclasses import dataclass # pyright: ignore[reportShadowedImports]

from fastapi import WebSocket

from backend.rpc.session import RPCSession
from backend.views.view import View, Session

__all__ = ["HandlerContext"]



@dataclass(slots=True)
class HandlerContext:
    ws: WebSocket          # FastAPI WebSocket; transport for this handler
    rpcSession: RPCSession # Transport session (handshake, pending tasks, etc.)
    view: View             # Authoritative UI state
    session: Session       # Inference state

    def __repr__(self) -> str:
        # Avoid dumping the WebSocket object in logs
        return (
            f"HandlerContext(ws=<WebSocket>), "
            f"rpcSession={self.rpcSession!r}, "
            f"view={self.view!r}, session={self.session!r}"
        )
