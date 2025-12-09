# backend/rpc/messages.py
from __future__ import annotations
from typing import Any
from collections.abc import Mapping
from pydantic import ValidationError
from backend.rpc.models import RPCMessage, Gen
from backend.core.ids import uuidv7
from backend.core.jsonutils import serializeError
from backend.app.config import pickBudgetMs
from backend.app.globals import config

__all__ = [
    "createWelcomeMessage", "createAckMessage", "createErrorMessage",
    "createStateUpdateMessage", "createReplyMessage",
]



def _requireGen(props: Mapping[str, Any]) -> Gen:
    try:
        return Gen.model_validate(props["gen"])
    except KeyError:
        raise ValueError("props.gen is required") from None
    except ValidationError as err:
        raise TypeError("props.gen must be a valid Gen") from err



def _requireDict(dct: Mapping[str, Any], key: str, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
    val = dct.get(key, default if default is not None else {})
    if not isinstance(val, dict):
        raise TypeError(f"props.{key} must be a dict")
    return val



def createWelcomeMessage(props: dict[str, Any], opts: dict[str, Any] | None = None) -> RPCMessage:
    if not isinstance(props, dict):
        raise TypeError("props must be a dict")
    
    gen = _requireGen(props)
    payload = _requireDict(props, "payload", default={})

    return RPCMessage(
        id=uuidv7(),
        v="0.1",
        type="welcome",
        gen=gen,
        lane="sys",
        budgetMs=pickBudgetMs(opts),
        payload=payload,
    )



def createAckMessage(toMsg: RPCMessage, props: dict[str, Any]) -> RPCMessage:
    if not isinstance(toMsg, RPCMessage):
        raise TypeError("toMsg must be a valid RPCMessage")
    if not isinstance(props, dict):
        raise TypeError("props must be a dict")
    
    gen = _requireGen(props)

    return RPCMessage(
        id=uuidv7(),
        v="0.1",
        type="ack",
        budgetMs=int(config("protocol.ackWaitMs", 250)),
        gen=gen,
        route=toMsg.route,
        lane="sys",
        correlatesTo=toMsg.id,
        payload={},
    )



def createErrorMessage(toMsg: RPCMessage, props: dict[str, Any], opts: dict[str, Any] | None = None) -> RPCMessage:
    if not isinstance(toMsg, RPCMessage):
        raise TypeError("toMsg must be a valid RPCMessage")
    if not isinstance(props, dict):
        raise TypeError("props must be a dict")
    
    gen = _requireGen(props)
    payload = _requireDict(props, "payload", default={})
    
    # Build error payload - props have precedence over payload
    errorPayload = {
        "code": props.get("code", payload.get("code", "UNKNOWN_ERROR")),
        "message": props.get("message", payload.get("message", "")),
        "err": serializeError(props.get("err", payload.get("err"))),
        "retryable": bool(props.get("retryable", payload.get("retryable", False))),
    }

    if not isinstance(errorPayload["code"], str):
        raise TypeError("code or payload.code must be a string with readable error code")
    
    return RPCMessage(
        id=uuidv7(),
        v="0.1",
        type="error",
        gen=gen,
        route=toMsg.route,
        lane="sys",
        correlatesTo=toMsg.id,
        budgetMs=pickBudgetMs(opts),
        payload=errorPayload,
    )



def createReplyMessage(toMsg: RPCMessage, props: dict[str, Any], opts: dict[str, Any] | None = None) -> RPCMessage:
    if not isinstance(toMsg, RPCMessage):
        raise TypeError("toMsg must be a valid RPCMessage")
    if not isinstance(props, dict):
        raise TypeError("props must be a dict")
    
    gen = _requireGen(props)
    payload = _requireDict(props, "payload") # No default - reply should include payload
    
    return RPCMessage(
        id=uuidv7(),
        v="0.1",
        type="reply",
        correlatesTo=toMsg.id,
        idempotencyKey=toMsg.idempotencyKey,
        route=toMsg.route,
        lane=toMsg.lane,
        gen=gen,
        budgetMs=pickBudgetMs(opts),
        payload=payload,
    )



def createStateUpdateMessage(
    toMsg: RPCMessage,
    props: dict[str, Any],
    opts: dict[str, Any] | None = None
) -> RPCMessage:
    if not isinstance(toMsg, RPCMessage):
        raise TypeError("toMsg must be a valid RPCMessage")
    if not isinstance(props, dict):
        raise TypeError("props must be a dict")

    gen = _requireGen(props)
    payload = _requireDict(props, "payload", default={})

    return RPCMessage(
        id=uuidv7(),
        v="0.1",
        type="stateUpdate",
        correlatesTo=toMsg.id,
        route=toMsg.route,
        lane=toMsg.lane,
        gen=gen,
        budgetMs=pickBudgetMs(opts),
        payload=payload,
    )
