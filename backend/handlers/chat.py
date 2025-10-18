# backend/handlers/chat.py
from __future__ import annotations
import asyncio
import logging

from backend.app import state
from backend.core.ids import uuidv7, uuid_10, uuid_12
from backend.core.time import nowMonotonicMs
from backend.handlers.context import HandlerContext
from backend.rpc.messages import createErrorMessage, createReplyMessage
from backend.rpc.models import RPCMessage, Route
from backend.rpc.transport import sendRPCMessage
from backend.views.view import Session

logger = logging.getLogger(__name__)



# NOTE(single-socket): we store subs in a session-level set and fanout via ctx.ws only.
# Multiple sockets bound to the same session won’t all receive updates.
async def handleSubscribeChatThread(ctx: HandlerContext, msg: RPCMessage):
    # Register subscriber
    ctx.session.chat["subs"].add(msg.id)

    # Initial snapshot
    thread = ctx.session.chat
    snapshot = {
        "kind": "threadSnapshot",
        "threadId": thread["threadId"],
        "order": list(thread["order"]),
        "headers": dict(thread["headers"]),
    }
    await sendRPCMessage(ctx.ws, RPCMessage(
        id=uuidv7(),
        v="0.1",
        type="stateUpdate",
        correlatesTo=msg.id,
        lane=msg.lane,
        gen=ctx.rpcSession.gen(),
        payload=snapshot,
    ))



async def handleRequestChatStart(ctx: HandlerContext, msg: RPCMessage):
    """
    Request: chat.start@1 -> starts a pipeline run and returns {runId}
    Payload/args: text (required), model/temperature/.. (optional)
    Effects: Emits threadDelta/messageDelta over chat.thread@1
    """
    llm = state.SERVICES.get("llm")
    if llm is None:
        await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
            "gen": ctx.rpcSession.currentGeneration(),
            "payload": {"code":"SERVICE_UNAVAILABLE", "message":"LLM driver is not available"}
        }))
        return

    text = (msg.args or [None])[0] or msg.payload.get("text")
    if not isinstance(text, str) or not text.strip():
        await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
            "gen": ctx.rpcSession.currentGeneration(),
            "payload": {"code":"BAD_REQUEST", "message":"'text' is required"}
        }))
        return

    runId = uuid_10("r_")

    # Input stage: persist user message
    user_oid = _appendMessage(ctx.session, role="user", content=text, status="final", runId=runId)
    await _pushThreadUpdate(ctx, {
        "kind":"threadDelta",
        "op":"insert",
        "at": len(ctx.session.chat["order"])-1,
        "oids":[user_oid],
        "headers": {user_oid: ctx.session.chat["headers"][user_oid]},
    })
    await _pushThreadUpdate(ctx, {
        "kind":"messageDelta",
        "oid":user_oid,
        "text":text,
        "fields":{"status":"final"}
    })

    # Build prompt (createQuery stage)
    promptMsgs = _buildPromptFromHistory(ctx.session, text)

    # Assistant placeholder (streaming)
    asst_oid = _appendMessage(ctx.session, role="assistant", content="", status="streaming", runId=runId)
    await _pushThreadUpdate(ctx, {
        "kind":"threadDelta",
        "op":"insert",
        "at": len(ctx.session.chat["order"])-1,
        "oids":[asst_oid],
        "headers": {asst_oid: ctx.session.chat["headers"][asst_oid]},
    })

    # Kick off the driver stream in background, reply immediately with runId
    await sendRPCMessage(ctx.ws, createReplyMessage(msg, {
        "gen": ctx.rpcSession.currentGeneration(),
        "payload": {"runId": runId}
    }))

    async def _run():
        try:
            assistantChunks: list[str] = []
            hadError = False
            wasCancelled = False
            payload = msg.payload or {}

            async for event in llm.streamChat(
                promptMsgs,
                model=payload.get("model", ""),
                temperature=payload.get("temperature", 0.8),
                max_tokens=payload.get("max_tokens", 256),
                top_p=payload.get("top_p"),
                extra=payload.get("extra"),
            ):
                if msg.id in ctx.rpcSession.cancelled:
                    wasCancelled = True
                    break

                if event.get("error"):
                    hadError = True
                    _updateMessage(ctx.session, asst_oid, status="error")
                    await _pushThreadUpdate(ctx, {"kind":"messageDelta", "oid":asst_oid, "fields":{"status":"error"}})
                    break

                if event.get("done"):
                    break

                ch = (event.get("choices") or [{}])[0]
                delta = (ch.get("delta") or {}).get("content") or ""
                if not delta:
                    continue
                assistantChunks.append(delta)
                _updateMessage(ctx.session, asst_oid, append=delta)
                await _pushThreadUpdate(ctx, {"kind":"messageDelta", "oid":asst_oid, "textDelta":delta})

            # Finalize only if we didn't error or cancel
            if not hadError and not wasCancelled:
                finalText = "".join(assistantChunks)
                _updateMessage(ctx.session, asst_oid, setText=finalText, status="final")
                await _pushThreadUpdate(ctx, {"kind":"messageDelta", "oid":asst_oid, "text":finalText, "fields":{"status":"final"}})
        
        except asyncio.CancelledError:
            _updateMessage(ctx.session, asst_oid, status="error")
            await _pushThreadUpdate(ctx, {"kind":"messageDelta", "oid":asst_oid, "fields":{"status":"error"}})
        except Exception as err:
            _updateMessage(ctx.session, asst_oid, status="error")
            await _pushThreadUpdate(ctx, {"kind":"messageDelta", "oid":asst_oid, "fields":{"status":"error"}})
            logger.exception("chat.start pipeline error: %s", err)
    
    task = asyncio.create_task(_run())
    ctx.rpcSession.pending[msg.id] = task
    task.add_done_callback(lambda _t, lRpcSession=ctx.rpcSession, lMsgId=msg.id: (lRpcSession.pending.pop(lMsgId, None), lRpcSession.cancelled.discard(lMsgId)))



async def handleSubscribeChat(ctx: HandlerContext, msg: RPCMessage):
    """
    Subscribe: chat@1 (streaming, cancellable)
    """
    llm = state.SERVICES.get("llm")
    if llm is None:
        await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
            "gen": ctx.rpcSession.currentGeneration(),
            "payload": {"code":"SERVICE_UNAVAILABLE", "message": "LLM driver is not available"}
        }))
        return

    # Build OpenAI-style messages from payload
    userTurn = {
        "id": msg.id + ".u",
        "role": msg.payload.get("role", "user"),
        "text": msg.payload.get("text", ""),
    }

    # NOTE: This role passthrough/flip is intentional to match current frontend expectations.
    # TODO: Revisit when the view layer lands; align with canonical roles and remove this shim.
    messages = [{
        "role": ("assistant" if userTurn["role"] == "assistant" else "user"),
        "content": userTurn["text"]
    }]

    async def run():
        assistantChunks: list[str] = []
        try:
            async for event in llm.streamChat(
                messages,
                model=msg.payload.get("model", ""),
                temperature=msg.payload.get("temperature", 0.8),
                max_tokens=msg.payload.get("max_tokens", 256),
                top_p=msg.payload.get("top_p"),
                extra=msg.payload.get("extra"),
            ):
                if msg.id in ctx.rpcSession.cancelled:
                    break

                if event.get("error"):
                    await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
                        "gen": ctx.rpcSession.currentGeneration(),
                        "payload": {"code":"ERR_LLM_STREAM", "message": event.get("error"), "err": event},
                    }))
                    break

                if event.get("done"):
                    break

                ch = (event.get("choices") or [{}])[0]
                delta = (ch.get("delta") or {}).get("content") or ""
                if not delta:
                    continue
                assistantChunks.append(delta)
                await sendRPCMessage(ctx.ws, RPCMessage(
                    id=uuidv7(),
                    v="0.1",
                    type="stateUpdate",
                    lane=msg.lane,
                    gen=ctx.rpcSession.gen(),
                    correlatesTo=msg.id,
                    payload={"delta": delta}
                ))
        except asyncio.CancelledError:
            pass
        except Exception as err:
            await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
                "gen": ctx.rpcSession.currentGeneration(),
                "payload": {"code":"ERR_LLM", "message":str(err), "err": err, "retryable": True},
            }))
        else:
            fullText = "".join(assistantChunks)
            await sendRPCMessage(ctx.ws, RPCMessage(
                id=uuidv7(),
                v="0.1",
                type="stateUpdate",
                lane=msg.lane,
                gen=ctx.rpcSession.gen(),
                correlatesTo=msg.id,
                payload={"text": fullText, "delta": "", "done": True},
            ))
    
    task = asyncio.create_task(run())
    ctx.rpcSession.pending[msg.id] = task
    task.add_done_callback(lambda _t, lSession=ctx.rpcSession, lMsgId=msg.id: (lSession.pending.pop(lMsgId, None), lSession.cancelled.discard(lMsgId)))



def _buildPromptFromHistory(sess: Session, userText: str):
    histPolicy = sess.chat["historyPolicy"]
    order = sess.chat["order"]
    msgs = sess.chat["messages"]

    # Take tails by role
    turns = []
    for oid in order:
        msg = msgs.get(oid)
        if not msg:
            continue
        if msg["role"] in ("user", "assistant") and msg.get("content"):
            turns.append({"role": msg["role"], "content": msg["content"]})
    
    # Keep last N pairs (userTail/assistantTail)
    userKept = []
    asstKept = []
    kept = []
    for it in reversed(turns):
        if it["role"] == "user" and len(userKept) < histPolicy["userTail"]:
            userKept.append(it)
            kept.append(it)
        elif it["role"] == "assistant" and len(asstKept) < histPolicy["assistantTail"]:
            asstKept.append(it)
            kept.append(it)
        if len(userKept) >= histPolicy["userTail"] and len(asstKept) >= histPolicy["assistantTail"]:
            break
    kept = list(reversed(kept))

    # Append the new user input
    kept.append({"role": "user", "content": userText})
    return kept



def _updateMessage(sess: Session, oid: str, *, append: str | None = None, setText: str | None = None, status: str | None = None):
    msg = sess.chat["messages"].get(oid)
    if not msg:
        raise KeyError(f"message '{oid}' not found")
    if append:
        msg["content"] = (msg.get("content") or "") + append
    if setText is not None:
        msg["content"] = setText
    if status:
        msg["status"] = status
    # Keep header.preview coherent enough for the list
    if "content" in msg:
        pv = (msg.get("content") or "")[:200]
        sess.chat["headers"][oid]["preview"] = pv
    return msg



def _appendMessage(sess: Session, *, role: str, content: str, status: str, runId: str | None):
    oid = _newMsgOid()
    ts = nowMonotonicMs()
    sess.chat["messages"][oid] = {
        "oid": oid,
        "role": role,
        "content": content,
        "status": status,
        "runId": runId,
        "ts": ts,
    }
    preview = content[:200]
    sess.chat["headers"][oid] = {
        "role": role,
        "preview": preview,
        "ts": ts,
    }
    sess.chat["order"].append(oid)
    return oid



async def _pushThreadUpdate(ctx: HandlerContext, payload: dict):
    # NOTE(single-socket): fanout sends to ctx.ws only. If the session has multiple sockets,
    # only the originating one gets updates. To support multi-socket, track subs per-socket.
    for subId in list(ctx.session.chat["subs"]):
        try:
            await sendRPCMessage(ctx.ws, RPCMessage(
                id=uuidv7(),
                v="0.1",
                type="stateUpdate",
                route=Route(capability="chat.thread@1"),
                gen=ctx.rpcSession.gen(),
                correlatesTo=subId,
                payload=payload,
            ))
        except Exception:
            # If websocket closes, the wsEndpoint cleanup removes subs, so ignore here
            pass



def _newMsgOid() -> str:
    return uuid_12("m_")
