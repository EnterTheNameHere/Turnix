# backend/rpc/transport.py
from __future__ import annotations
import asyncio
import logging
from typing import Any

from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketState
from pydantic import ValidationError

from backend.app.globals import getPermissions, getMainSessionOrScram
from backend.core.auth import resolvePrincipal
from backend.core.errors import ReactorScramError
from backend.core.jsonutils import safeJsonDumps
from backend.core.permissions import GrantPermissionError
from backend.core.time import nowMonotonicMs
from backend.core.logging.handlers import getJSLogHandler
from backend.rpc.api import (
    getCapability, routeRequest, routeEmit,
    routeSubscribe, ActiveSubscription,
)
from backend.rpc.connection import RPCConnection, getRPCConnection
from backend.rpc.context import CallContext, EmitContext, SubscribeContext
from backend.rpc.logging import decideAndLog
from backend.rpc.messages import (
    createAckMessage, createWelcomeMessage, createErrorMessage,
    createStateUpdateMessage, createReplyMessage
)
from backend.rpc.models import RPCMessage, Gen
from backend.rpc.types import SubscriptionEntry, PendingRequestEntry
from backend.views.manager import viewManager
from backend.views.registry import viewRegistry
from backend.views.view import View

logger = logging.getLogger(__name__)



_DEFAULT_REQUEST_TIMEOUT_MS = 30_000 # If msg.budgetMs is None; TODO: Make this default on RPCMessage in future?



def _safeCancel(task: asyncio.Task | None) -> None:
    try:
        if task and not task.done():
            task.cancel()
    except Exception:
        pass



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



async def _ensureCapabilityOrError(ws: WebSocket, session: RPCConnection, msg: RPCMessage, capability: str) -> bool:
    """Resolve principal and enforce capability permission. Reply with error on denial."""
    try:
        principal = resolvePrincipal(msg)
        getPermissions().ensure(principal=principal, capability=capability)
        return True
    except GrantPermissionError as gperr:
        await sendRPCMessage(ws, createErrorMessage(msg, {
            "gen": session.gen(),
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
        rpcConnection: RPCConnection | None = None
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
                        # Bind by clientId - default single-player path
                        view, _ = viewRegistry.getOrCreateViewForClient(clientId)

                    viewManager.bind(ws=ws, view=view)
                    
                    # Enable JS log streaming once at first bind
                    getJSLogHandler().setReady(True)

                    rpcConnection = getRPCConnection(view.id, clientId, "session-1")
                    gen = rpcConnection.newGeneration()

                    # Send snapshot state with welcome
                    view.patchState(rpcConnection.state)
                    await sendRPCMessage(ws, createWelcomeMessage({
                        "gen": gen,
                        "payload": view.snapshot(),
                    }))
                    continue

                # Handshake is required!
                if rpcConnection is None:
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
                    currGenNum = rpcConnection.genNum

                    # If someone sends a stale clientReady, ignore it (but ACK it)
                    if msg.gen and hasattr(msg.gen, "num"):
                        clientReportedGen = getattr(msg.gen, "num", None)
                        if isinstance(clientReportedGen, int) and clientReportedGen != currGenNum:
                            logger.debug(
                                "Stale clientReady for gen='%s' (current='%s'); ACKing and ignoring",
                                clientReportedGen, currGenNum,
                            )
                            await sendRPCMessage(ws, createAckMessage(msg, {"gen": rpcConnection.gen()}))
                            continue

                    # Ignore duplicate clientReady from this gen (ACK anyway)
                    if currGenNum in rpcConnection.clientReadyGens:
                        logger.debug(
                            "Duplicate clientReady for gen='%s' (viewId='%s', clientId='%s'). ACKing and ignoring",
                            currGenNum, getattr(view, "id", "?"), clientId,
                        )
                        await sendRPCMessage(ws, createAckMessage(msg, {"gen": rpcConnection.gen()}))
                        continue
                    
                    rpcConnection.clientReadyGens.add(currGenNum)
                    if len(rpcConnection.clientReadyGens) > 256:
                        # Keep only the most recent 64 gens
                        base = currGenNum - 64
                        rpcConnection.clientReadyGens = {gg for gg in rpcConnection.clientReadyGens if gg >= base}

                    loaded = msg.payload.get("loaded") or []
                    failed = msg.payload.get("failed") or []
                    modsHash = msg.payload.get("modsHash")

                    rpcConnection.lastClientReady = {
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
                                "mods": rpcConnection.lastClientReady["mods"],
                            }
                        })
                    except Exception:
                        # non-fatal, keep going
                        pass
                    
                    logger.info(
                        "[clientReady] accepted for gen='%s' (viewId='%s', clientId='%s') mods: loaded='%d' failed='%d'",
                        currGenNum, getattr(view, "id", "?"), clientId, len(loaded), len(failed),
                    )

                    await sendRPCMessage(ws, createAckMessage(msg, {"gen": rpcConnection.gen()}))
                    continue

                # Immediate ack for non-control messages
                if msgType not in ("ack", "heartbeat"):
                    await sendRPCMessage(ws, createAckMessage(msg, {"gen": rpcConnection.gen()}))

                if msgType == "heartbeat":
                    rpcConnection.lastHeartbeatTs = nowMonotonicMs()
                    await sendRPCMessage(ws, createAckMessage(msg, {"gen": rpcConnection.gen()}))
                    continue

                # Cancel request or subscription
                if msgType in ("cancel", "unsubscribe"):
                    corrId = msg.correlatesTo
                    if corrId:
                        # Cancel an in-flight request
                        if corrId in rpcConnection.pending:
                            rpcConnection.cancelled.add(corrId)
                            pendingEntry: PendingRequestEntry | None = rpcConnection.pending.pop(corrId)
                            task: asyncio.Task[Any] | None = getattr(pendingEntry, "task", None)
                            _safeCancel(task)
                            # Proactively notify client that the request ended via cancellation
                            origMsg: RPCMessage | None = getattr(pendingEntry, "msg", None)
                            try:
                                if isinstance(origMsg, RPCMessage):
                                    await sendRPCMessage(ws, createErrorMessage(origMsg, {
                                        "gen": rpcConnection.gen(),
                                        "payload": {
                                            "code": "REQUEST_CANCELLED",
                                            "message": "Request cancelled by client",
                                            "retryable": False,
                                        },
                                    }))
                            except Exception:
                                logger.warning("Failed to notify request '%s' was cancelled.", corrId, exc_info=True)
                        # Cancel an active subscription
                        if corrId in rpcConnection.subscriptions:
                            subscriptionEntry: SubscriptionEntry = rpcConnection.subscriptions.pop(corrId) # type: ignore[assignment]
                            try:
                                subscriptionEntry.signal.set()
                                if callable(subscriptionEntry.onCancel):
                                    subscriptionEntry.onCancel()
                            finally:
                                if not subscriptionEntry.task.done():
                                    subscriptionEntry.task.cancel()
                    continue

                if msgType == "subscribe":
                    capability = (msg.route.capability or "").strip() if msg.route else ""
                    if not getCapability(capability):
                        logger.warning("Unknown capability for subscribe: '%s'", capability)
                        await sendRPCMessage(ws, createErrorMessage(msg, {
                            "gen": rpcConnection.gen(),
                            "payload": {"code":"CAPABILITY_NOT_FOUND","message":f"Unknown capability '{capability}' for subscribe."}
                        }))
                        continue

                    # Permission check
                    if not await _ensureCapabilityOrError(ws, rpcConnection, msg, capability):
                        continue
                    
                    # Build SubscribeCtx over HandlerContext with a push → WS bridge
                    signal = asyncio.Event()
                    
                    # Snapshot values for this request to avoid late-binding bugs in the async task...
                    localGen = rpcConnection.gen()
                    localWs = ws
                    localRPC = rpcConnection
                    localMsg = msg
                    def _pushToWs(ev: dict) -> None:
                        # Fire-and-forget. Never block capability.    
                        async def _send(ev: dict = ev, ws: WebSocket = localWs, rpc: RPCConnection = localRPC, gen: Gen = localGen, msg=localMsg):
                            # Drop if generation changed (client re-hello'd) or socket is gone
                            if rpc.genNum != gen.num or ws.application_state != WebSocketState.CONNECTED:
                                return
                            try:
                                await sendRPCMessage(ws, createStateUpdateMessage(msg, {
                                    "gen": localGen,
                                    "payload": ev or {},
                                }))
                            except Exception:
                                logger.debug("subscribe push send failed", exc_info=True)
                        asyncio.create_task(_send())
                    
                    ctx = SubscribeContext(id=msg.id, origin=msg.origin, signal=signal, _push=_pushToWs)
                    
                    try:
                        desc: ActiveSubscription = await routeSubscribe(
                            capability, (msg.path or ""), msg.payload or {}, ctx,
                        )
                    except Exception as err:
                        await sendRPCMessage(ws, createErrorMessage(msg, {
                            "gen": rpcConnection.gen(),
                            "payload": {"code":"SUBSCRIBE_ERROR","message":str(err),"err":err,"retryable": False}
                        }))
                        continue
                    
                    # Optional initial payload
                    if desc.initial is not None:
                        await sendRPCMessage(ws, createStateUpdateMessage(msg, {
                            "gen": rpcConnection.gen(),
                            "payload": desc.initial,
                        }))
                    
                    # Keep a trivial "liveness" task that just waits on the signal
                    async def _hold():
                        try:
                            await signal.wait()
                        except asyncio.CancelledError:
                            pass
                    
                    entry = asyncio.create_task(_hold(), name=f"sub:{capability}:{msg.id}")
                    rpcConnection.subscriptions[msg.id] = SubscriptionEntry(entry, desc.onCancel, signal)
                    continue

                if msgType == "request":
                    capability = (msg.route.capability or "").strip() if msg.route else ""
                    if not getCapability(capability):
                        logger.warning("Unknown capability for request: %r", capability)
                        await sendRPCMessage(ws, createErrorMessage(msg, {
                            "gen": rpcConnection.gen(),
                            "payload": {"code":"CAPABILITY_NOT_FOUND","message":"Unknown capability/route for request"}
                        }))
                        continue
                    
                    # Permission check
                    if not await _ensureCapabilityOrError(ws, rpcConnection, msg, capability):
                        continue
                    
                    ctx = CallContext(id=msg.id, origin=msg.origin)
                    timeoutMs = msg.budgetMs if isinstance(msg.budgetMs, int) and msg.budgetMs > 0 else _DEFAULT_REQUEST_TIMEOUT_MS
                    
                    # Snapshot values for this request to avoid late-binding bugs in the async task...
                    localGen = rpcConnection.gen()
                    localWs = ws
                    localRPC = rpcConnection
                    localMsg = msg
                    localCapability = capability
                    localCtx = ctx
                    localTimeoutMs = timeoutMs
                    async def _runRequest(
                        ws: WebSocket = localWs,
                        rpc: RPCConnection = localRPC,
                        gen: Gen = localGen,
                        msg: RPCMessage = localMsg,
                        capability: str = localCapability,
                        ctx: CallContext = localCtx,
                        timeoutMs: int = localTimeoutMs
                    ):
                        try:
                            result = await asyncio.wait_for(
                                routeRequest(capability, (msg.path or ""), msg.args or [], ctx),
                                timeout=timeoutMs / 1000.0
                            )
                            # Drop if generation changed (client re-hello'd) or socket is gone
                            if rpc.genNum != gen.num or ws.application_state != WebSocketState.CONNECTED:
                                return
                            # Ensure dict payload.
                            payload = result if isinstance(result, dict) else {"result": result}
                            try:
                                # Drop if cancelled
                                if msg.id in rpc.cancelled:
                                    return
                                await sendRPCMessage(ws, createReplyMessage(msg, {
                                    "gen": gen,
                                    "payload": payload,
                                }))
                            except Exception:
                                logger.debug("_runRequest sending reply failed (likely disconnect happened)", exc_info=True)
                                return
                        except asyncio.TimeoutError as err:
                            # Drop if generation changed (client re-hello'd) or socket is gone
                            if rpc.genNum != gen.num or ws.application_state != WebSocketState.CONNECTED:
                                return
                            try:
                                # Drop if cancelled
                                if msg.id in rpc.cancelled:
                                    return
                                await sendRPCMessage(ws, createErrorMessage(msg, {
                                    "gen": gen,
                                    "payload": {
                                        "code": "REQUEST_TIMEOUT",
                                        "message": f"Request exceeded {timeoutMs} ms.",
                                        "err": err,
                                        "retryable": True,
                                    },
                                }))
                            except Exception:
                                logger.debug("_runRequest sending TimeoutError notification failed (likely disconnect happened)", exc_info=True)
                                return
                        except asyncio.CancelledError:
                            # Cancellation reply is handled by cancel branch
                            raise
                        except Exception as err:
                            # Drop if generation changed (client re-hello'd) or socket is gone
                            if rpc.genNum != gen.num or ws.application_state != WebSocketState.CONNECTED:
                                return
                            try:
                                # Drop if cancelled
                                if msg.id in rpc.cancelled:
                                    return
                                await sendRPCMessage(ws, createErrorMessage(msg, {
                                    "gen": gen,
                                    "payload": {"code":"REQUEST_ERROR","message":str(err),"err":err,"retryable":False}
                                }))
                            except Exception:
                                logger.debug("_runRequest sending Error notification failed (likely disconnect happened)", exc_info=True)
                                return
                        finally:
                            rpc.pending.pop(msg.id, None)
                    
                    task = asyncio.create_task(_runRequest(), name=f"request:{capability}:{msg.id}")
                    rpcConnection.pending[msg.id] = PendingRequestEntry(task=task, msg=msg)
                    continue

                if msgType == "emit":
                    capability = (msg.route.capability or "").strip() if msg.route else ""
                    if not getCapability(capability):
                        logger.warning("Unknown capability for emit: %r", capability)
                        await sendRPCMessage(ws, createErrorMessage(msg, {
                            "gen": rpcConnection.gen(),
                            "payload": {"code":"CAPABILITY_NOT_FOUND","message":"Unknown capability/route for emit"}
                        }))
                        continue
                    
                    # Permission check
                    if not await _ensureCapabilityOrError(ws, rpcConnection, msg, capability):
                        continue
                    
                    session = getMainSessionOrScram()
                    ctx = EmitContext(id=msg.id, origin=msg.origin)
                    # Fire-and-forget. Errors are swallowed inside routeEmit
                    routeEmit(capability, (msg.path or ""), msg.payload or {}, ctx) 
                    continue

        finally:
            if rpcConnection is not None:
                # Cancel pending requests
                for existingPendingEntry in rpcConnection.pending.values():
                    _safeCancel(existingPendingEntry.task)
                # Cancel subs and call onCancel
                for corrId, existingSubscriptionEntry in list(rpcConnection.subscriptions.items()):
                    try:
                        existingSubscriptionEntry.signal.set()
                        if callable(existingSubscriptionEntry.onCancel):
                            existingSubscriptionEntry.onCancel()
                    finally:
                        if not existingSubscriptionEntry.task.done():
                            existingSubscriptionEntry.task.cancel()
                        rpcConnection.subscriptions.pop(corrId, None)
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
