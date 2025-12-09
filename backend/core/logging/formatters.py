# backend/core/logging/formatters.py
from __future__ import annotations

import logging

from backend.core.jsonutils import safeJsonDumps
from backend.core.redaction import redactText
from .context import getLogContext



class RedactingFormatter(logging.Formatter):
    """
    Wraps another formatter and redacts the final formatted string.
    """
    def __init__(self, inner: logging.Formatter):
        super().__init__()
        self._inner = inner
    
    def format(self, record: logging.LogRecord) -> str:
        rendered = self._inner.format(record)
        try:
            return redactText(rendered)
        except Exception:
            # Never crash logging due to redaction failure
            return rendered



class JsonFormatter(logging.Formatter):
    """One-line JSON records for files and JS streaming."""
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": int(record.created * 1000),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "ctx": getLogContext() or {},
            "proc": {"pid": record.process, "name": record.processName},
            "thread": {"id": record.thread, "name": record.threadName},
        }

        if record.exc_info:
            # Safely extract exception information
            excType = record.exc_info[0]
            excValue = record.exc_info[1]
            try:
                typ = getattr(excType, "__name__", type(excType).__name__)
                msg = str(excValue)
                stack = self.formatException(record.exc_info)
            except Exception:
                typ, msg, stack = "Error", "format failed", None
            base["exc"] = {"type": typ, "message": msg, "stack": stack}
        
        return safeJsonDumps(base)



class DevFormatter(logging.Formatter):
    """Human-friendly console formatter (dev mode)."""
    def format(self, record: logging.LogRecord) -> str:
        ctx = getLogContext()
        ctxStr = ""
        if ctx:
            reqId = ctx.get("requestId") or ctx.get("messageId")
            viewId = ctx.get("viewId")
            clientInstanceId = ctx.get("clientInstanceId")
            md = []
            if reqId:
                md.append(str(reqId))
            if viewId:
                md.append(str(viewId))
            if clientInstanceId:
                md.append(str(clientInstanceId))
            if md:
                ctxStr = " [" + "/".join(md) + "]"
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            msg += "\n" + str(record.stack_info)
        return f"{record.levelname}: [{record.name}] {msg}{ctxStr}"



class ContextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = getLogContext() or {}
        record.viewId = ctx.get("viewId", "")
        record.modId = ctx.get("modId", "")
        record.requestId = ctx.get("requestId")
        return super().format(record)
