# backend/rpc/logging.py
from __future__ import annotations
from typing import Any, Literal, Mapping

from backend.rpc.models import RPCMessage
from backend.core.redaction import redactText
from backend.core.jsonutils import safeJsonDumps
from backend.app.globals import config
from backend.core.dictpath import getByPath
from backend.core.ops import evaluateOp

import logging
logger = logging.getLogger(__name__)

__all__ = ["shouldLogRpcMessage", "decideAndLog"]



def _shorten(text: str, *, maxLen: int = 4096) -> str:
    text = redactText(text)
    return (text[:maxLen] + "…") if (maxLen and len(text) > maxLen) else text



def _rpcLogCfg(direction: Literal["incoming", "outgoing"]) -> Mapping[str, Any]:
    side = config("debug.backend.rpc", {}) # May not be a dict
    if not isinstance(side, dict):
        return {"log": False}
    cfg = side.get("incomingMessages") if direction == "incoming" else side.get("outgoingMessages")
    return cfg if isinstance(cfg, dict) else {"log": False}



def shouldLogRpcMessage(msg: RPCMessage | None, cfg: Mapping[str, Any]) -> bool:
    if not (isinstance(cfg, Mapping) and cfg.get("log", False)):
        return False
    
    msgType = getattr(msg, "type", None)
    ignoreTypes = cfg.get("ignoreTypes")
    if isinstance(ignoreTypes, list) and msgType and msgType in ignoreTypes:
        return False
    
    rules = cfg.get("rules")
    if not isinstance(rules, list):
        # No type rule => fallback to global log, which by this time is true, so log message...
        return True
    
    # Find rule by exact type or wildcard
    rule = next((rl for rl in rules if rl.get("type") in (msgType, "*")), None)
    if not rule:
        # If no rule for this type, log it
        return True
    
    tests = rule.get("tests")
    if isinstance(tests, list):
        for test in tests:
            if not isinstance(test, dict):
                continue
            prop = test.get("property")
            op = test.get("op")
            value = test.get("value")
            left = getByPath(msg, prop) if msg and prop else None
            if op and evaluateOp(left, op, value):
                return bool(test.get("shouldLog", True))
    
    return bool(rule.get("shouldLog", False))



def decideAndLog(
    direction: Literal["incoming", "outgoing"],
    *,
    rpcMessage: RPCMessage | None,
    text: str | None = None,
    bytesLen: int | None = None
) -> None:
    # Hard guard on pathological text sizes. _shorten is running redaction which needs a whole text
    # to avoid mistakingly not redacting a sliced part of text, so it's better to just display nothing...
    maxChars = int(config("debug.backend.rpc.maxPreviewChars", 1_000_000))
    if text is not None and len(text) > maxChars: # 1MB
        logger.debug(f"[RPC] {direction}: <{len(text)} chars, suppressed>")
        return
    
    cfg = _rpcLogCfg(direction)
    if not cfg.get("log", False):
        return
    
    # If we have a validated RPCMessage, evaluate rules on the model
    if rpcMessage is not None:
        if not shouldLogRpcMessage(rpcMessage, cfg):
            return
        preview = text if text is not None else safeJsonDumps(rpcMessage)
        logger.debug(f"[RPC] {direction}: {_shorten(preview)}")
        return
    
    # No model → best effort
    if bytesLen is not None:
        logger.debug(f"[RPC] {direction}: <{bytesLen} bytes>")
        return
    if text is not None:
        logger.debug(f"[RPC] {direction}: {_shorten(text)}")
        return
    
    # Nothing to log
    return
