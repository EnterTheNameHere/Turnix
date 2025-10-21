from __future__ import annotations
import asyncio
import logging
from typing import Any

from fastapi import WebSocket

from backend.views.manager import viewManager
from backend.core.ids import uuidv7
from backend.rpc.session import getRPCConnection
from backend.rpc.transport import sendRPCMessage

logger = logging.getLogger(__name__)

__all__ = ["pushEvent", "pushEventToView"]



# ----------------------------------------------
#                   Public API
# ----------------------------------------------

async def pushEvent(capability: str, payload: dict[str, Any], *, override_shouldLog=None):
    """
    Broadcast a server-initiated "emit" RPC to all connected views.

    Each WebSocket receives its own RPCMessage:
      - type="emit"
      - route.capability=capability
      - payload=payload
      - gen = the current generation for that view's RPCSession
      - override_shouldLog: bool | None - Override the default logging behavior for this payload.
        • None  → Follow the default behavior as defined by decideAndLog()
        • True  → Force logging even if globally disabled
        • False → Do not log this payload even if globally enabled; useful to prevent infinite logging loops
    """
    tasks = []
    for viewId, sockets in viewManager.iterViews():
        for ws in sockets:
            tasks.append(_sendOne(ws, viewId, capability, payload, override_shouldLog=override_shouldLog))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)



async def pushEventToView(viewId: str, capability: str, payload: dict[str, Any]):
    """
    Broadcast a server-initiated "emit" RPC to a single viewId.
    """
    sockets = viewManager.socketsForView(viewId)
    if not sockets:
        return
    await asyncio.gather(*[_sendOne(ws, viewId, capability, payload) for ws in sockets], return_exceptions=True)



async def _sendOne(ws: WebSocket, viewId: str, capability: str, payload: dict[str, Any], *, override_shouldLog: bool | None = None):
    """
    Build and send a single RPCMessage(emit) to a WebSocket, using the socket's cookie clientId if present.
      
    - override_shouldLog: bool | None - Override the default logging behavior for this payload.
        • None  → Follow the default behavior as defined by decideAndLog()
        • True  → Force logging even if globally disabled
        • False → Do not log this payload even if globally enabled; useful to prevent infinite logging loops
    """
    from backend.rpc.models import RPCMessage, Route

    try:
        try:
            clientId = ws.cookies.get("clientId")
        except Exception:
            clientId = None
        
        sess = getRPCConnection(viewId, clientId, "session-1")
        msg = RPCMessage(
            id=uuidv7(),
            v="0.1",
            type="emit",
            gen=sess.gen(),
            route=Route(capability=capability, object=None),
            payload=payload or {},
        )
        await sendRPCMessage(ws, msg, override_shouldLog=override_shouldLog)
    except Exception:
        # Never crash on broadcast of a single socket
        logger.debug("pushEvent failed for viewId=%r clientId=%r", viewId, clientId, exc_info=True)
