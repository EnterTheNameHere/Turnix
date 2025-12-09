# backend/app/trace_ws.py
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.app.globals import getTraceHub
from backend.core.jsonutils import safeJsonDumps

router = APIRouter()



@router.websocket("/ws/trace")
async def traceWebSocket(ws: WebSocket) -> None:
    await ws.accept()
    hub = getTraceHub()
    snapshot, queue = hub.subscribe()
    
    try:
        # Send existing buffer first so the viewer gets some context.
        for record in snapshot:
            await ws.send_text(safeJsonDumps(record))
        
        # Then stream new records as they come in...
        while True:
            record = await queue.get()
            await ws.send_text(safeJsonDumps(record))
    except WebSocketDisconnect:
        # Normal disconnect from viewer.
        pass
    finally:
        hub.unsubscribe(queue)
