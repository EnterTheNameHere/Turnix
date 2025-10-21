# backend/rpc/transport.py
from __future__ import annotations
import logging
from fastapi import FastAPI, WebSocket
from pydantic import ValidationError

from backend.app.state import PERMS
from backend.core.auth import resolvePrincipal
from backend.core.errors import ReactorScramError
from backend.core.jsonutils import safeJsonDumps
from backend.core.permissions import GrantPermissionError
from backend.core.time import nowMonotonicMs
from backend.handlers.context import HandlerContext
from backend.handlers.objects import handleRequestObject
from backend.handlers.register import SUBSCRIBE_HANDLERS, REQUEST_HANDLERS, EMIT_HANDLERS
from backend.core.logging.handlers import getJSLogHandler
from backend.rpc.logging import decideAndLog
from backend.rpc.messages import createAckMessage, createWelcomeMessage, createErrorMessage
from backend.rpc.models import RPCMessage, Route
from backend.rpc.session import RPCConnection, getRPCConnection
from backend.views.manager import viewManager
from backend.views.registry import viewRegistry
from backend.views.view import View

logger = logging.getLogger(__name__)



async def sendRPCMessage(ws: WebSocket, message: RPCMessage, *, override_shouldLog: bool | None = None):
    """
    Send an RPCMessage over WebSocket.

    Parameters
      - ws: WebSocket - Active websocket connection
      - message: RPCMessage - Message to send over the WebSocket
      - override_shouldLog: bool | None - Override the default logging behavior for this message.
        • None  → Follow the default behavior as defined by decideAndLog()
        • True  → Force logging even if globally disabled
        • False → Do not log this message even if globally enabled
    """
    jsonText = safeJsonDumps(message)
    
    if override_shouldLog is None or override_shouldLog is True:
        decideAndLog("outgoing", rpcMessage=message, text=jsonText)

    await ws.send_text(jsonText)



async def sendText(ws: WebSocket, text: str):
    decideAndLog("outgoing", rpcMessage=None, text=text)
    await ws.send_text(text)



async def sendBytes(ws: WebSocket, data: bytes):
    decideAndLog("outgoing", rpcMessage=None, text=None, bytesLen=len(data))
    await ws.send_bytes(data)



async def _ensureCapabilityOrError(ws: WebSocket, sess: RPCConnection, msg: RPCMessage, capability: str) -> bool:
    """Resolve principal and enforce capability permission. Reply with error on denial."""
    try:
        principal = resolvePrincipal(msg)
        PERMS.ensure(principal=principal, capability=capability)
        return True
    except GrantPermissionError as gperr:
        await sendRPCMessage(ws, createErrorMessage(msg, {
            "gen": sess.gen(),
            "payload": {
                "code": gperr.code,
                "message": gperr.message,
                "retryable": gperr.retryable,
                "err": gperr.extra,
            }
        }))
        return False



def mountWebSocket(app: FastAPI):
    @app.websocket("/ws")
    async def wsEndpoint(ws: WebSocket):
        await ws.accept()
        sessLocal: RPCConnection | None = None
        view: View | None = None
        clientId: str | None = None
        
        try:
            while True:
                event = await ws.receive()
                if event["type"] == "websocket.disconnect":
                    break
                
                if event["type"] != "websocket.receive":
                    continue
                
                if "text" in event and event["text"] is not None:
                    raw = event["text"]

                    # Soft guard for pathological sizes
                    if isinstance(raw, str) and len(raw) > 1_000_000:
                        decideAndLog("incoming", rpcMessage=None, text="<suppressed: too large>")
                        await sendText(ws, safeJsonDumps({
                            "type": "error",
                            "payload": {
                                "code": "FRAME_TOO_LARGE",
                                "message": "payload too large",
                            }
                        }))
                        # TODO: We might close websocket with 1009 if it happens too many times.
                        continue

                elif "bytes" in event and event["bytes"] is not None:
                    # Best-effort log (no rule eval; not JSON)
                    decideAndLog("incoming", rpcMessage=None, text=None, bytesLen=len(event["bytes"]))
                    continue
                else:
                    continue

                try:
                    msg = RPCMessage.model_validate_json(raw)
                    decideAndLog("incoming", rpcMessage=msg, text=raw)
                except ValidationError as verr:
                    logger.debug(f"Invalid JSON", exc_info=True)
                    # Best-effort log for broken input (no rules)
                    decideAndLog("incoming", rpcMessage=None, text=raw)
                    continue
                
                msgType = msg.type

                # TODO: warn on message with lane = "noLaneSet" or "noValidRouteLane"

                # ----- Handshake -----
                if msgType == "hello":
                    clientId = ws.cookies.get("clientId")
                    if not clientId or not clientId.strip():
                        clientId = viewRegistry.ensureClientId(ws.cookies)

                    payload = msg.payload or {}
                    viewId = payload.get("viewId")
                    viewToken = payload.get("viewToken")
                    _clientInstanceId = payload.get("clientInstanceId")   # reserved for reconnection logic
                    _lastKnownGen = int(payload.get("lastKnownGen") or 0) # reserved for reconnection logic

                    if viewId and viewToken and viewRegistry.validateToken(viewId, clientId, viewToken):
                        # Invariant - a validated token must reference an existing View
                        view = viewRegistry.getViewById(viewId)
                        if not view:
                            raise ReactorScramError(
                                f"Token validated but View {viewId!r} not found. "
                                f"This is an invariant violation. View is dead! "
                                f"Runtime declared a state of emergency. "
                                f"Reality's integrity irrecoverably fragmented. "
                                f"May the garbage collector have mercy."
                            )
                        # Keep binding fresh
                        viewRegistry.bindClientToView(clientId, viewId)
                    else:
                        # Bind by clientId - default singleplayer path
                        view, _ = viewRegistry.getOrCreateViewForClient(clientId)

                    viewManager.bind(ws=ws, view=view)
                    
                    # Enable JS log streaming once at first bind
                    getJSLogHandler().setReady(True)

                    sessLocal = getRPCConnection(view.id, clientId, "session-1")
                    gen = sessLocal.newGeneration()

                    # Send snapshot state with welcome
                    view.patchState(sessLocal.state)
                    await sendRPCMessage(ws, createWelcomeMessage({
                        "gen": gen,
                        "payload": view.snapshot(),
                    }))
                    continue

                # Handshake is required!
                if sessLocal is None:
                    # Ignore anything before hello
                    continue

                # View must exist at this point
                if view is None:
                    raise ReactorScramError(
                        f"View for client {clientId} not found! This shouldn't happen! "
                        f"The stability of the application is not guaranteed! Jokes are no longer funny! "
                        f"Dogs and cats are living together! We should've given penguins the voting rights when we had chance!")

                if msgType == "clientReady":
                    # Frontend declares it has finished loading/initializing
                    currGenNum = sessLocal.genNum

                    # If someone sends a stale clientReady, ignore it (but ACK it)
                    if msg.gen and hasattr(msg.gen, "num"):
                        clientReportedGen = getattr(msg.gen, "num", None)
                        if isinstance(clientReportedGen, int) and clientReportedGen != currGenNum:
                            logger.debug(
                                "Stale clientReady for gen='%s' (current='%s'); ACKing and ignoring",
                                clientReportedGen, currGenNum,
                            )
                            await sendRPCMessage(ws, createAckMessage(msg, {"gen": sessLocal.gen()}))
                            continue

                    # Ignore duplicate clientReady from this gen (ACK anyway)
                    if currGenNum in sessLocal.clientReadyGens:
                        logger.debug(
                            "Duplicate clientReady for gen='%s' (viewId='%s', clientId='%s'). ACKing and ignoring",
                            currGenNum, getattr(view, "id", "?"), clientId,
                        )
                        await sendRPCMessage(ws, createAckMessage(msg, {"gen": sessLocal.gen()}))
                        continue
                    
                    sessLocal.clientReadyGens.add(currGenNum)
                    if len(sessLocal.clientReadyGens) > 256:
                        # Keep only the most recent 64 gens
                        base = currGenNum - 64
                        sessLocal.clientReadyGens = {gg for gg in sessLocal.clientReadyGens if gg >= base}

                    loaded = msg.payload.get("loaded") or []
                    failed = msg.payload.get("failed") or []
                    modsHash = msg.payload.get("modsHash")

                    sessLocal.lastClientReady = {
                        "gen": msg.gen,
                        "ts": msg.ts,
                        "mods": {
                            "loaded": loaded,
                            "failed": failed,
                            "modsHash": modsHash,                    
                        }
                    }

                    try:
                        view.patchState({
                            "clientReady": {
                                "gen": currGenNum,
                                "ts": msg.ts,
                                "mods": sessLocal.lastClientReady["mods"],
                            }
                        })
                    except Exception:
                        # non-fatal, keep going
                        pass
                    
                    logger.info(
                        "[clientReady] accepted for gen='%s' (viewId='%s', clientId='%s') mods: loaded='%d' failed='%d'",
                        currGenNum, getattr(view, "id", "?"), clientId, len(loaded), len(failed),
                    )

                    await sendRPCMessage(ws, createAckMessage(msg, {"gen": sessLocal.gen()}))
                    continue

                # Immediate ack for non-control messages
                if msgType not in ("ack", "heartbeat"):
                    await sendRPCMessage(ws, createAckMessage(msg, {"gen": sessLocal.gen()}))

                if msgType == "heartbeat":
                    sessLocal.lastHeartbeatTs = nowMonotonicMs()
                    await sendRPCMessage(ws, createAckMessage(msg, {"gen": sessLocal.gen()}))
                    continue

                # Cancel request or subscription
                if msgType in ("cancel", "unsubscribe"):
                    corrId = msg.correlatesTo
                    if corrId and corrId in sessLocal.pending:
                        sessLocal.cancelled.add(corrId)
                        sessLocal.pending[corrId].cancel()
                        sessLocal.pending.pop(corrId, None)
                    if corrId and corrId in sessLocal.subscriptions:
                        sessLocal.subscriptions[corrId].cancel()
                        sessLocal.subscriptions.pop(corrId, None)
                    try:
                        # TODO: make this non main session when we implement multiple sessions for view
                        view.mainSession.chat["subs"].discard(corrId)
                    except AttributeError as aerr:
                        logger.exception("Chat unsubscribe on missing mainSession/subs (corrId=%r): %s", corrId, aerr)
                    continue

                if msgType == "subscribe":
                    capability = (msg.route.capability or "").strip() if msg.route else ""
                    handler = SUBSCRIBE_HANDLERS.get(capability)
                    if not handler:
                        logger.warning("Unknown capability for subscribe: %r", capability)
                        await sendRPCMessage(ws, createErrorMessage(msg, {
                            "gen": sessLocal.gen(),
                            "payload": {"code":"CAPABILITY_NOT_FOUND","message":"Unknown capability/route for subscribe"}
                        }))
                        continue

                    # Permission check
                    if not await _ensureCapabilityOrError(ws, sessLocal, msg, capability):
                        continue

                    await handler(HandlerContext(ws=ws, rpcConnection=sessLocal, view=view, session=view.mainSession), msg)
                    continue

                if msgType == "request":
                    obj = (msg.route.object if isinstance(msg.route, Route) else None) or None
                    if obj:
                        # TODO: Enforce object-level permission if we use them
                        await handleRequestObject(HandlerContext(ws=ws, rpcConnection=sessLocal, view=view, session=view.mainSession), msg)
                        continue
                    capability = (msg.route.capability or "").strip() if msg.route else ""
                    handler = REQUEST_HANDLERS.get(capability)
                    if not handler:
                        logger.warning("Unknown capability for request: %r", capability)
                        await sendRPCMessage(ws, createErrorMessage(msg, {
                            "gen": sessLocal.gen(),
                            "payload": {"code":"CAPABILITY_NOT_FOUND","message":"Unknown capability/route for request"}
                        }))
                        continue
                    
                    # Permission check
                    if not await _ensureCapabilityOrError(ws, sessLocal, msg, capability):
                        continue

                    await handler(HandlerContext(ws=ws, rpcConnection=sessLocal, view=view, session=view.mainSession), msg)
                    continue

                if msgType == "emit":
                    capability = (msg.route.capability or "").strip() if msg.route else ""
                    handler = EMIT_HANDLERS.get(capability)
                    if not handler:
                        logger.warning("Unknown capability for emit: %r", capability)
                        await sendRPCMessage(ws, createErrorMessage(msg, {
                            "gen": sessLocal.gen(),
                            "payload": {"code":"CAPABILITY_NOT_FOUND","message":"Unknown capability/route for emit"}
                        }))
                        continue
                    
                    # Permission check
                    if not await _ensureCapabilityOrError(ws, sessLocal, msg, capability):
                        continue

                    await handler(HandlerContext(ws=ws, rpcConnection=sessLocal, view=view, session=view.mainSession), msg)
                    continue

        finally:
            if sessLocal is not None:
                for task in list(sessLocal.pending.values()):
                    task.cancel()
                for task in list(sessLocal.subscriptions.values()):
                    task.cancel()
            try:
                viewManager.removeViewForWs(ws)
                if not viewManager.viewIds():
                    getJSLogHandler().setReady(False)
            except Exception:
                pass

            try:
                await ws.close()
            except Exception:
                pass
