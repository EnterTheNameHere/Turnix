# backend/rpc/session.py
from __future__ import annotations
import secrets, time, asyncio
from typing import Any

from backend.rpc.models import RPCMessage, Gen

__all__ = ["RPCConnection", "RPC_CONNECTIONS", "getRPCConnection"]



class RPCConnection:
    """
    Holds per-connection state: idempotency cache, pending jobs, etc.
    key: (viewId, clientId | None, sessionId | None)
    """
    _MAX_CACHE = 512
    
    def __init__(self, key: tuple[str, str | None, str | None]):
        self.key = key
        self.idCache: set[str] = set()
        self.replyCache: dict[str, RPCMessage] = {}
        self.pending: dict[str, asyncio.Task[Any]] = {}
        self.cancelled: set[str] = set()
        self.subscriptions: dict[str, asyncio.Task[Any]] = {} # correlatesTo -> task
        self.state = {
            "serverMessage": "Welcome to Turnix RPC",
            "serverBootTs": time.time(),
        }
        self.genNum = 0
        self.genSalt = ""
        # Last clientReady payload
        self.lastClientReady: dict | None = None
        self.lastHeartbeatTs = 0
        self.clientReadyGens: set[int] = set()

    def newGeneration(self) -> dict:
        self.genNum += 1
        self.genSalt = secrets.token_hex(4)
        return {"num": self.genNum, "salt": self.genSalt}
    
    def currentGeneration(self) -> dict:
        return {"num": self.genNum, "salt": self.genSalt}
    
    def gen(self) -> Gen:
        """Return current connection generation as a validated model."""
        return Gen.model_validate(self.currentGeneration())

    def dedupeKey(self, msg: RPCMessage) -> str:
        return msg.idempotencyKey or msg.id

    def remember(self, key: str):
        """Stores an idempotency key; prunes arbitrarily when beyong soft limit."""
        self.idCache.add(key)
        if len(self.idCache) > self._MAX_CACHE:
            # simple prune: drop ~1/4
            for _ in range(len(self.idCache) // 4):
                self.idCache.pop()
    
    def putReply(self, key: str, reply: RPCMessage):
        """Caches a reply by idempotency key; size-bounded with simple pruning."""
        self.replyCache[key] = reply
        if len(self.replyCache) > self._MAX_CACHE:
            # drop arbitrary 1/4
            for key in list(self.replyCache.keys())[:len(self.replyCache)//4]:
                self.replyCache.pop(key, None)

    def cancelPending(self) -> None:
        """Cancels all pending request tasks."""
        for task in list(self.pending.values()):
            task.cancel()
        self.pending.clear()
        self.cancelled.clear()
    
    def cancelSubscriptions(self) -> None:
        """Cancels all subscription tasks."""
        for task in list(self.subscriptions.values()):
            task.cancel()
        self.subscriptions.clear()



RPC_CONNECTIONS: dict[tuple[str, str | None, str | None], RPCConnection] = {}



def getRPCConnection(viewId: str, clientId: str | None, sessionId: str | None) -> RPCConnection:
    key = (viewId, clientId, sessionId)
    rpcConn = RPC_CONNECTIONS.get(key)
    if not rpcConn:
        rpcConn = RPCConnection(key)
        RPC_CONNECTIONS[key] = rpcConn
    return rpcConn
