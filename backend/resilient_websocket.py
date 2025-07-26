import asyncio
import json
from fastapi import WebSocket, WebSocketDisconnect
from typing import Any, Optional
from core.stringjson import safe_json_dumps, safe_json_loads

import logging
logger = logging.getLogger(__name__)

class WebSocketTimeoutError(Exception):
    pass

class WebSocketRetryError(Exception):
    pass

class ResilientWebSocket:
    def __init__(self, websocket: WebSocket, timeout: float = 10.0, maxRetries = 3):
        self._websocket = websocket
        self._timeout = timeout
        self._maxRetries = maxRetries
        self._last_payload: Optional[dict] = None
    
    async def send(self, data: dict):
        self._last_payload = data
        retries = 0

        while retries <= self._maxRetries:
            try:
                await asyncio.wait_for(
                    self._websocket.send_text(safe_json_dumps(data)),
                    timeout=self._timeout,
                )
                return # Successful send
            except asyncio.TimeoutError:
                retries += 1
                if retries > self._maxRetries:
                    raise WebSocketTimeoutError("WebSocket send() timed out.")
            except Exception as e:
                # TODO: Report this unknown exception better
                raise WebSocketRetryError(f"WebSocket failed to send() data: {e}")

    async def receive(self) -> Any:
        try:
            return safe_json_loads(await self._websocket.receive_text())
        except WebSocketDisconnect as e:
            raise
        except json.JSONDecodeError as e:
            raise WebSocketRetryError(f"Invalid JSON received: {e}")

    async def retry(self):
        if self._last_payload is None:
            raise WebSocketRetryError("No previous payload to retry.")
        await self.send(self._last_payload)
    
    async def close(self, code: int = 1000, reason: str = ""):
        await self._websocket.close(code=code, reason=reason)

    @property
    def raw(self) -> WebSocket:
        return self._websocket
