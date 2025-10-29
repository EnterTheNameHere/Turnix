# backend/handlers/context.py
from __future__ import annotations
from dataclasses import dataclass # pyright: ignore[reportShadowedImports]

from fastapi import WebSocket

from backend.rpc.connection import RPCConnection
from backend.views.view import View
from backend.sessions.session import Session

__all__ = ["HandlerContext"]



@dataclass(slots=True)
class HandlerContext:
    ws: WebSocket                # FastAPI WebSocket; transport for this handler
    rpcConnection: RPCConnection # Transport connection (handshake, pending tasks, etc.)
    view: View                   # Authoritative UI state
    session: Session             # Inference state

    def __repr__(self) -> str:
        # Avoid dumping the WebSocket object in logs
        return (
            f"HandlerContext(ws=<WebSocket>), "
            f"rpcConnection={self.rpcConnection!r}, "
            f"view={self.view!r}, session={self.session!r}"
        )
