# backend/handlers/objects.py
from __future__ import annotations
import asyncio
from typing import Any

from backend.core.dictpath import getByPath, setByPath
from backend.core.jsonutils import tryJSONify
from backend.handlers.context import HandlerContext
from backend.rpc.models import RPCMessage
from backend.rpc.messages import createErrorMessage, createReplyMessage

OBJECTS: dict[str, Receiver] = {}



class Receiver:
    """Executes get/set/call/snapshot on a concrete Python object."""
    def __init__(self, oid: str, obj: object, *, kind: str = "generic"):
        self.oid = oid
        self.obj = obj
        self.kind = kind
        self.version = 0
        # Allowlist of method names frontend side can call
        self.allowedMethods: set[str] = set()
    
    def allow(self, *names: str) -> Receiver:
        self.allowedMethods.update(names)
        return self
    
    def get(self, path: str | None) -> Any:
        if not path:
            return None
        return tryJSONify(getByPath(self.obj, path, None))

    def set(self, path: str, value: Any) -> int:
        setByPath(self.obj, path, value, createIfMissing=False)
        self.version += 1
        return self.version
    
    def call(self, method: str, args: list[Any] | None, kwargs: dict[str, Any] | None) -> Any:
        if method not in self.allowedMethods:
            raise PermissionError(f"Method '{method}' not allowed on '{self.kind}'")
        fn = getattr(self.obj, method, None)
        if not callable(fn):
            raise AttributeError(f"No method '{method}'")
        res = fn(*(args or []), **(kwargs or {}))
        # Awaitable support without forcing async everywhere
        if asyncio.iscoroutine(res):
            async def _awaitAndBump():
                rr = await res
                self.version += 1
                return tryJSONify(rr)
            return _awaitAndBump()
        self.version += 1
        return tryJSONify(res)

    def snapshot(self) -> dict[str, Any]:
        if hasattr(self.obj, "snapshot") and callable(self.obj.snapshot): # type: ignore[attr-defined]
            snap = self.obj.snapshot() # type: ignore[attr-defined]
        else:
            snap = {}

        return {"oid": self.oid, "kind": self.kind, "version": self.version, "state": snap}



def registerObject(rcv: Receiver) -> str:
    OBJECTS[rcv.oid] = rcv
    return rcv.oid



def getObject(oid: str) -> Receiver:
    rcv = OBJECTS.get(oid)
    if not rcv:
        raise KeyError(f"Unknown object '{oid}'")
    return rcv



async def handleRequestObject(ctx: HandlerContext, msg: RPCMessage):
    """
    Request: route.object=<oid>
    op: "get" | "set" | "call" | "snapshot"
    - get: payload.path -> value
    - set: payload.path, payload.value -> {version}
    - call: payload.method, payload.args?, payload.kwargs? -> result
    - snapshot: -> {oid, kind, version, state}
    """
    from backend.rpc.transport import sendRPCMessage
    
    oid = (msg.route.object or "").strip() if msg.route else ""
    try:
        rcv = getObject(oid)
    except KeyError:
        await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
            "gen": ctx.rpcSession.gen(),
            "payload": {"code": "OBJECT_NOT_FOUND", "message": f"Unknown object '{oid}'"},
        }))
        return
    
    op = (msg.op or "").strip()
    payload = msg.payload or {}

    try:
        if op == "get":
            path = payload.get("path") or msg.path
            value = rcv.get(path)
            await sendRPCMessage(ctx.ws, createReplyMessage(msg, {
                "gen": ctx.rpcSession.gen(),
                "payload": {"value": value, "version": rcv.version},
            }))
            return

        if op == "set":
            path = payload.get("path") or msg.path
            if not isinstance(path, str) or not path:
                raise ValueError("set requires 'path'")
            version = rcv.set(path, payload.get("value"))
            await sendRPCMessage(ctx.ws, createReplyMessage(msg, {
                "gen": ctx.rpcSession.gen(),
                "payload": {"ok": True, "version": version},
            }))
            return
        
        if op == "call":
            raw_method = payload.get("method")
            if not isinstance(raw_method, str):
                await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
                    "gen": ctx.rpcSession.gen(),
                    "payload": {
                        "code": "OBJECT_CALL_FAIL_INVALID_METHOD_NAME",
                        "message": f"'method' to call must be a string ('{oid}')"
                    },
                }))
                return
            method = raw_method.strip()
            if not method:
                await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
                    "gen": ctx.rpcSession.gen(),
                    "payload": {
                        "code": "OBJECT_CALL_FAIL_METHOD_NAME_EMPTY",
                        "message": f"'method' to call cannot be empty ('{oid}')"
                    },
                }))
                return
            
            raw_args = payload.get("args", msg.args)
            args = list(raw_args) if isinstance(raw_args, (list,tuple)) else []
            
            raw_kwargs: dict = payload.get("kwargs", {})
            kwargs = raw_kwargs if isinstance(raw_kwargs, dict) else {}
            
            res = rcv.call(method, args, kwargs)
            if asyncio.iscoroutine(res):
                res = await res
            await sendRPCMessage(ctx.ws, createReplyMessage(msg, {
                "gen": ctx.rpcSession.gen(),
                "payload": {"result": res, "version": rcv.version},
            }))
            return
        
        if op == "snapshot":
            snap = rcv.snapshot()
            await sendRPCMessage(ctx.ws, createReplyMessage(msg, {
                "gen": ctx.rpcSession.gen(),
                "payload": snap,
            }))
            return
        
        await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
            "gen": ctx.rpcSession.gen(),
            "payload": {"code": "UNKNOWN_OP", "message": f"Unsupported op '{op}' for object '{rcv.kind}'"},
        }))
    
    except Exception as err:
        await sendRPCMessage(ctx.ws, createErrorMessage(msg, {
            "gen": ctx.rpcSession.gen(),
            "payload": {"code": "OBJECT_ERROR", "message": str(err), "err": err},
        }))
