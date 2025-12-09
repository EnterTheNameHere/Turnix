# backend/core/logging/util.py
from __future__ import annotations

import logging

from backend.app.globals import configBool



class ModLogger:
    """Tiny sugar for mod loggers with trace()."""
    def __init__(self, logger: logging.Logger, traceEnabled: bool) -> None:
        self._log = logger
        self._traceEnabled = traceEnabled
    
    def debug(self, msg: str, *args, **kwargs): self._log.debug(msg, *args, **kwargs)
    def info(self, msg: str, *args, **kwargs): self._log.info(msg, *args, **kwargs)
    def log(self, msg: str, *args, **kwargs): self._log.info(msg, *args, **kwargs)
    def warn(self, msg: str, *args, **kwargs): self._log.warning(msg, *args, **kwargs)
    def warning(self, msg: str, *args, **kwargs): self._log.warning(msg, *args, **kwargs)
    def error(self, msg: str, *args, **kwargs): self._log.error(msg, *args, **kwargs)
    def exception(self, msg: str, *args, **kwargs): self._log.exception(msg, *args, **kwargs)
    def trace(self, msg: str, *args, **kwargs):
        if self._traceEnabled:
            self._log.debug("[TRACE] " + msg, *args, **kwargs)

def getLogger(name: str, side: str = "") -> logging.Logger:
    return logging.getLogger(f"{side}.{name}" if side else name)

def getModLogger(modId: str) -> ModLogger:
    traceEnabled = configBool("debug.tracingEnabled", False)
    return ModLogger(logging.getLogger(f"mod.{modId}"), traceEnabled)

def getProfilerLogger() -> logging.Logger:
    return logging.getLogger("profiler")
