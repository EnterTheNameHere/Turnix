# backend/handlers/gm.py
from __future__ import annotations
import asyncio
import logging
import time

from starlette.websockets import WebSocketDisconnect

from backend.core.ids import uuidv7
from backend.core.time import nowMonotonicMs
from backend.handlers.context import HandlerContext
from backend.rpc.messages import createReplyMessage
from backend.rpc.models import RPCMessage
from backend.rpc.session import RPCSession
from backend.rpc.transport import sendRPCMessage

logger = logging.getLogger(__name__)



async def handleSubscribeGMWorld(ctx: HandlerContext, msg: RPCMessage):
    """
    Subscribe: gm.world@1
    """
    async def streamWorld(correlatesTo: str, lane: str, session: RPCSession):
        try:
            while True:
                await asyncio.sleep(2.0)
                payload = { "turn": int(time.time()), "actors": ["goblin", "player"] }
                await sendRPCMessage(ctx.ws, RPCMessage(
                    v="0.1",
                    id=(uuidv7()),
                    type="stateUpdate",
                    correlatesTo=correlatesTo,
                    lane=lane,
                    gen=session.gen(),
                    payload=payload,
                ))
        except asyncio.CancelledError:
            # Normal shutdown
            pass
        except WebSocketDisconnect:
            # Client went away; stop quietly
            pass
        except Exception as err:
            logger.debug("gm.world stream stopped unexpectedly: %s", err)
        finally:
            # Ensure we drop the subscription record
            ctx.rpcSession.subscriptions.pop(correlatesTo, None)

    correlatesTo = msg.id
    lane = msg.lane
    task = asyncio.create_task(streamWorld(correlatesTo=correlatesTo, lane=lane, session=ctx.rpcSession))
    ctx.rpcSession.subscriptions[correlatesTo] = task



async def handleRequestGMNarration(ctx: HandlerContext, msg: RPCMessage):
    """
    Request: gm.narration@1 (simple, cancellable)
    """
    async def run():
        start = nowMonotonicMs()
        try:
            # Quick demo latency but honor budget if smaller
            toSleep = min(200, int(msg.budgetMs or 3_000)) / 1_000.0
            await asyncio.sleep(toSleep)
            if msg.id in ctx.rpcSession.cancelled:
                return
            action = (msg.args or ["(silence)"])[0]
            text = f"The GM considers your action {action!r} and responds with a twist."
            reply = createReplyMessage(msg, {
                "gen": ctx.rpcSession.gen(),
                "payload": {"text": text, "spentMs": nowMonotonicMs() - start},
            })
            ctx.rpcSession.putReply(ctx.rpcSession.dedupeKey(msg), reply)
            await sendRPCMessage(ctx.ws, reply)
        except asyncio.CancelledError:
            pass
    
    task = asyncio.create_task(run())
    ctx.rpcSession.pending[msg.id] = task
    task.add_done_callback(lambda _t, lSession=ctx.rpcSession, lMsgId=msg.id: (lSession.pending.pop(lMsgId, None), lSession.cancelled.discard(lMsgId)))
