# backend/handlers/register.py
from __future__ import annotations
from typing import Any
from collections.abc import Awaitable, Callable # pyright: ignore[reportShadowedImports] - one of our requirement ships typings extra, but Python 3.12 already includes them

from backend.handlers.context import HandlerContext
from backend.handlers.gm import handleRequestGMNarration, handleSubscribeGMWorld
from backend.handlers.http_client import handleRequestHttpClient
from backend.handlers.chat import handleRequestChatStart, handleSubscribeChatThread, handleSubscribeChat
from backend.rpc.models import RPCMessage

AsyncHandler = Callable[[HandlerContext, RPCMessage], Awaitable[Any]]

REQUEST_HANDLERS: dict[str, AsyncHandler] = {
    "http.client@1":    handleRequestHttpClient,
    "gm.narration@1":   handleRequestGMNarration,
    "chat.start@1":     handleRequestChatStart,
}
SUBSCRIBE_HANDLERS: dict[str, AsyncHandler] = {
    "chat@1":           handleSubscribeChat,
    "gm.world@1":       handleSubscribeGMWorld,
    "chat.thread@1":    handleSubscribeChatThread,
}
EMIT_HANDLERS: dict[str, AsyncHandler] = {
    
}
