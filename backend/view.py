from typing import Callable
from backend.resilient_websocket import ResilientWebSocket, WebSocketRetryError
from backend.session import Session
import itertools
import asyncio
import time

import logging
logger = logging.getLogger(__name__)

class RPCError(Exception):
    def __init__(self, message, code="UNKNOWN", details=None):
        super().__init__(message)
        self.code = code
        self.details = details

class View:
    def __init__(self, viewId: str, clientId: int):
        self._viewId = viewId
        self._clientId = clientId
        self._socket = None
        self._rpcHandlers = {}
        self._pendingRequests = {}
        self._requestIdCounter = itertools.count(1)
        self._sessions = {}
        self._modsEventsHandlers = {}
        self.registerRpcHandler("registerHook", self.registerFrontendHook)
        self.registerRpcHandler("sendUserMessage", self.sendUserMessage)

    async def sendUserMessage(self, data):
        sessionId = data.get("sessionId")
        if sessionId is None:
            raise ValueError("'sessionId' is required.")

        session = self._sessions.get(sessionId)
        if session is None:
            raise ValueError(f"Session '{sessionId}' does not exist.")

        await session.sendUserMessage(data)


    async def registerFrontendHook(self, data):
        sessionId = data.get("sessionId")
        if sessionId is None:
            raise ValueError("'sessionId' is required.")
        
        session = self._sessions.get(sessionId)
        if session is None:
            raise ValueError(f"Session '{sessionId}' does not exist.")

        await session.registerFrontendHook(data)

    def _makeRequestId(self) -> str:
        return f"{self.viewId}@{self.clientId}#py:{next(self._requestIdCounter)}"
    
    def assignSocket(self, rws: ResilientWebSocket | None) -> None:
        self._socket = rws
    
    def getSocket(self) -> ResilientWebSocket | None:
        return self._socket
    
    async def send(self, data: dict) -> None:
        logger.debug(f"[view:{self.key}] Sending data to frontend: {data}")
        if not self._socket:
            raise WebSocketRetryError(f"[view:{self.key}] Socket not assigned")
        return await self._socket.send(data)

    async def handleRequest(self, message: dict):
        if not self._socket:
            raise WebSocketRetryError(f"[view:{self.key}] Socket not assigned")
        
        logger.debug(f"[view:{self.key}] Received request: {message}")

        handler = self._rpcHandlers.get(message["action"])
        requestId = message.get("requestId")

        if handler:
            try:
                result = await handler(message["data"])
                # TODO: Handle serialization error
                await self.send({
                    "type": "backendReply",
                    "requestId": requestId,
                    "data": result,
                    "success": True,
                })
            except Exception as e:
                logger.exception(f"[view:{self.key}] Error occured in RPC handler.")
                # TODO: Handle serialization error
                await self.send({
                    "type": "backendReply",
                    "requestId": requestId,
                    "error": str(e),
                    "success": False,
                })
        else:
            logger.warning(f"[view:{self.key}] No RPC handler for {message['name']}")
            # TODO: Handle serialization error
            await self.send({
                "type": "backendReply",
                "requestId": requestId,
                "error": f"No handler for '{message['action']}'",
                "success": False,
            })

    async def handleEmit(self, message: dict):
        if not self._socket:
            raise WebSocketRetryError(f"[view:{self.key}] Socket not assigned")
        
        logger.debug(f"[view:{self.key}] Received emit: {message}")
        
        handler = self._rpcHandlers.get(message["action"])
        if handler:
            try:
                await handler(message["data"])
            except Exception:
                logger.exception(f"[view:{self.key}] Error occured in RPC emit handler '{message['action']}'.")
        else:
            logger.warning(f"[view:{self.key}] No RPC emit handler for '{message['action']}'")

    async def handleReplyOrError(self, message: dict):
        requestId = message.get("requestId")
        if not requestId:
            logger.warning(f"[view:{self.key}] RPC reply or error is missing 'requestId'.")
            return
        
        future = self._pendingRequests.pop(requestId, None)
        if not future:
            logger.warning(f"[view:{self.key}] No pending request for request '{requestId}'.")
            return
        
        if message["type"] == "frontendReply":
            future.set_result(message.get("data"))
        elif message["type"] == "frontendError":
            error = message.get("error", {})
            future.set_exception(RPCError(
                message=error.get("message", "Unknown error"),
                code=error.get("code", "UNKNOWN"),
                details=error.get("details"),
            ))

    async def sendAndWait(self, action: str, data: dict, timeout: float = 10.0) -> dict:
        if not self._socket:
            raise WebSocketRetryError(f"[view:{self.key}] Socket not assigned")

        requestId = self._makeRequestId()
        future = asyncio.get_event_loop().create_future()
        self._pendingRequests[requestId] = future

        payload = {
            "type": "backendRequest",
            "action": action,
            "viewId": self.viewId,
            "clientId": self.clientId,
            "requestId": requestId,
            "securityToken": self.securityToken,
            "timestamp": int(time.time() * 1000),
            "data": data,
        }

        await self.send(payload)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pendingRequests.pop(requestId, None)
            raise TimeoutError(f"[view:{self.key}] RPC action '{action}' timed out after {timeout} seconds.")

    def registerRpcHandlers(self, handlers: dict[str, Callable]):
        self._rpcHandlers.update(handlers)

    def registerRpcHandler(self, action: str, handler: Callable):
        self._rpcHandlers[action] = handler

    @property
    def viewId(self):
        return self._viewId
    
    @property
    def clientId(self):
        return self._clientId
    
    @property
    def key(self):
        return (self.viewId, self.clientId)
    
    @property
    def securityToken(self):
        return "TODO"
    
    @property
    def sessions(self) -> dict[str, Session]:
        return self._sessions
    
    async def createOrRefreshMainSession(self):
        # If main session doesn't exist yet, create it.
        # A new request to create main session can be sent
        # if the frontend loses connection. In such case, refresh
        # the main session to the frontend.
        if self._sessions.get("main") is not None:
            
            return
        
        session = Session("main", self)
        self._sessions["main"] = session
        if self._socket is not None:
            await self._socket.send({
                "type": "backendEmit",
                "action": "onSessionCreated",
                "viewId": self.viewId,
                "clientId": self.clientId,
                "securityToken": self.securityToken,
                "data": {
                    "sessionId": session.sessionId,
                    "viewId": self.viewId,
                    "clientId": self.clientId,
                }
            })

