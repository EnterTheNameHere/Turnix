import asyncio
from fastapi import WebSocket, WebSocketDisconnect
from typing import Callable, Awaitable, Any
from core.stringjson import safe_json_dumps, safe_json_loads
from core.logger import getLogger
from backend.view import View

import logging
logger = logging.getLogger(__name__)

handlers: dict[str, Callable[[Any], Awaitable[Any]]] = {}
activeSocket: WebSocket | None = None
socketOnline = asyncio.Event()
pendingRequests: dict[str, dict] = {}

rpcHandlers: dict[str, Callable[[View, dict], Awaitable[Any]]] = {}

def registerNewRpc(name: str):
    def decorator(func: Callable[[View, dict], Awaitable[Any]]):
        rpcHandlers[name] = func
        return func
    return decorator

def registerRpc(name: str):
    def decorator(func: Callable[[Any], Awaitable[Any]]):
        handlers[name] = func
        return func
    return decorator

@registerRpc("frontendHookResponse")
async def handleFrontendHookResponse(payload):
    requestId = payload.get("requestId")
    name = payload.get("name")
    result = payload.get("result")
    error = payload.get("error")

    future = pendingRequests.get(requestId)
    if not future:
        logger.warning(f"Received response for unknown requestId '{requestId}', {payload}")
        return
    
    if error:
        logger.error(f"Frontend hook '{name}' failed with error '{error}', {payload}")
        future["reject"](error)
    else:
        future["resolve"](result)

@registerRpc("log")
async def handleJSLog(payload):
    logger = getLogger("js", side="frontend")
    level = payload.get("level", "info").lower()
    message = payload.get("message", "")
    source = payload.get("source", "unknown")

    logFunc = getattr(logger, level, logger.info)
    logFunc(f"[{source}] {message}")
    
async def websocketRpcEndpoint(websocket: WebSocket):
    global activeSocket
    await websocket.accept()
    activeSocket = websocket
    logger.info("WebSocket connected.")

    try:
        while True:
            message = await websocket.receive_text()
            data = safe_json_loads(message)
            
            if data["type"] == "request":
                await handleRequest(websocket, data)
            elif data["type"] == "event":
                await handleEvent(data)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected.")
        activeSocket = None

async def handleRequest(websocket: WebSocket, msg: dict):
    name = msg["name"]
    reqId = msg["id"]
    payload = msg.get("data", {})

    handler = handlers[name]
    if not handler:
        await websocket.send_text(safe_json_dumps({
            "type": "response",
            "id": reqId,
            "success": False,
            "error": f"Unknown handler method '{name}'"
        }))
        return
    
    try:
        result = await handler(payload)
        await websocket.send_text(safe_json_dumps({
            "type": "response",
            "id": reqId,
            "success": True,
            "data": result
        }))
    except Exception as e:
        await websocket.send_text(safe_json_dumps({
            "type": "response",
            "id": reqId,
            "success": False,
            "error": str(e)
        }))
        logger.error(f"Error in handler method '{name}': {e}")

async def handleEvent(msg: dict):
    name = msg["name"]
    payload = msg.get("data", {})
    handler = handlers[name]
    if handler:
        await handler(payload)

async def pushEvent(name: str, data: dict):
    if activeSocket:
        await activeSocket.send_text(safe_json_dumps({
            "type": "event",
            "name": name,
            "data": data
        }))
