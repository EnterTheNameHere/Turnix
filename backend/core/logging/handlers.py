# backend/core/logging/handlers.py
from __future__ import annotations
import asyncio
from collections import deque
import logging

from .formatters import JsonFormatter



class JSLogHandler(logging.Handler):
    """
    Streams logs to connected frontends via RPC "emit" messages.

    - Batches messages (size + time)
    - Apply back-pressure (requeue on failure, bounded)
    - Uses JSON formatter so the UI can parse structured entries
    - Publishes through backend.rpc.broadcast.pushEvent()
    """
    def __init__(self, *, maxQueue: int = 2000, batchSize: int = 100, flushMs: int = 250):
        super().__init__()
        # If maxQueue <= 0, treat as unbounded (deque maxlen=None)
        self._deque: deque[str] = deque(maxlen=maxQueue if maxQueue and maxQueue > 0 else None)
        self._batchSize = max(1, int(batchSize))
        self._flushMs = max(0, int(flushMs))
        self._ready = False
        self._flushTask: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # Default JSON formatter for UI consumption
        self.setFormatter(JsonFormatter())

    def emit(self, record: logging.LogRecord):
        try:
            payload = self.format(record)
        except Exception:
            # Never crash during logging
            payload = '{"level":"error","logger":"logging","msg":"format failed"}'
        
        # Deque append/popleft are thread-safe primitives
        self._deque.append(payload)

        # Schedule flush if ready and not already running
        if self._ready and self._flushTask is None and self._loop is not None:
            # Ensure scheduling from ANY thread
            self._loop.call_soon_threadsafe(self._ensureFlushTask)
    
    def _ensureFlushTask(self):
        if self._flushTask is None and self._ready and self._loop is not None and self._deque:
            # Create task on the captured loop
            self._flushTask = self._loop.create_task(self._flushLoop())

    async def _flushLoop(self):
        from backend.rpc.broadcast import pushEvent
        
        try:
            while self._ready and self._deque:
                batch: list[str] = []
                while self._deque and len(batch) < self._batchSize:
                    batch.append(self._deque.popleft())

                if not batch:
                    break

                try:
                    # Emit once per socket with capability "system.logs"
                    # Payload shape expected by frontend: {"entries": [jsonLineStr, ...]}
                    await pushEvent("system.logs", {"entries": batch}, override_shouldLog=False)
                except Exception:
                    # On failure, requeue items we can; if unbounded (maxlen=None), allow all back
                    if self._deque.maxlen is None:
                        for item in reversed(batch):
                            self._deque.appendleft(item)
                    else:
                        # Bounded: fit what we can
                        space = max(self._deque.maxlen - len(self._deque), 0)
                        for item in reversed(batch[:space]):
                            self._deque.appendleft(item)
                    await asyncio.sleep(0.25)

                if self._flushMs > 0:
                    await asyncio.sleep(self._flushMs / 1000.0)
        finally:
            self._flushTask = None
    
    def setReady(self, ready: bool = True):
        """
        Mark the handler ready and capture the current event loop. When flipping to
        ready, schedule a flush if there is backlog. When disabling, cancel the task.
        """
        if ready:
            # Capture loop on the calling async context
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            self._loop = loop
            self._ready = True
            if self._loop is not None and self._deque and self._flushTask is None:
                self._loop.call_soon_threadsafe(self._ensureFlushTask)
        else:
            self._ready = False
            # Cancel any pending task cleanly
            if self._flushTask is not None:
                self._flushTask.cancel()
                self._flushTask = None



# Singleton accessor
__jsHandler = JSLogHandler()



def getJSLogHandler() -> JSLogHandler:
    return __jsHandler



def setJSLoggingReady(ready: bool = True):
    """
    Sets JS logging readiness.
    """
    __jsHandler.setReady(ready)
