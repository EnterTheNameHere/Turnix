# backend/core/logging/setup.py
from __future__ import annotations
import logging
import logging.handlers

from backend.app.globals import config, configBool
from .formatters import DevFormatter, JsonFormatter, RedactingFormatter
from .filters import RecurringSuppressFilter
from .handlers import getJSLogHandler

__all__ = [
    "NO_PROPAGATE",
    "configureLogging",
    "getLogger",
    "getModLogger",
    "getProfilerLogger",
]



# Disable propagation from common libraries
NO_PROPAGATE = [
    "uvicorn", "uvicorn.access", "uvicorn.error",
    "fastapi", "concurrent.futures", "asyncio",
    "httpcore.connection", "httpcore.http11",
    "httpx"
]



def configureLogging():
    """
    Initiate the global logging configuration.

    Dev:
      - Console pretty logs (DEBUG)
      - JSON file log (DEBUG)
      - JS streaming (DEBUG)
    
    Prod:
      - Console INFO
      - JSON file logs INFO with rotation
      - JS streaming INFO
      - PII scrubbing active
      - Optional recurring suppression (toggle)
    """
    devMode = configBool("debug.devModeEnabled", True)
    rootLevel = logging.DEBUG if devMode else logging.INFO

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(rootLevel)
    
    for name in NO_PROPAGATE:
        logging.getLogger(name).propagate = False

    # Formatters
    devFmt = RedactingFormatter(DevFormatter())
    jsonFmt = RedactingFormatter(JsonFormatter())

    # Handlers
    consoleHandler = logging.StreamHandler()
    consoleHandler.setLevel(rootLevel)
    consoleHandler.setFormatter(devFmt)

    fileHandler = logging.handlers.RotatingFileHandler(
        "backend.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    fileHandler.setLevel(rootLevel)
    fileHandler.setFormatter(jsonFmt)

    jsHandler = getJSLogHandler()
    jsHandler.setLevel(rootLevel)
    jsHandler.setFormatter(jsonFmt)

    # Optional recurring suppression (disabled by default)
    if configBool("debug.suppressRecurringMessages.enabled", False):
        # Resolve summaryLevel string like "INFO" → logging.INFO, fallback safe
        levelName = str(config("debug.suppressRecurringMessages.summaryLevel", "INFO")).upper()
        summaryLevel = getattr(logging, levelName, logging.INFO)

        suppressFilter = RecurringSuppressFilter(
            windowSeconds=int(config("debug.suppressRecurringMessages.windowSeconds", 60)),
            maxPerWindow=int(config("debug.suppressRecurringMessages.maxPerWindow", 5)),
            summaryLevel=summaryLevel,
        )
        consoleHandler.addFilter(suppressFilter)
        jsHandler.addFilter(suppressFilter)
        fileHandler.addFilter(suppressFilter)

    # Attach handlers
    root.addHandler(consoleHandler)
    root.addHandler(fileHandler)
    root.addHandler(jsHandler)

    # Per-logger tweaks (reduce noise)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("fastapi").setLevel(logging.INFO)



def getLogger(name: str, side: str = ""):
    return logging.getLogger(f"{str(side).strip()}.{str(name).strip()}" if str(side).strip() else str(name).strip())



def getModLogger(modId: str):
    return logging.getLogger(f"mods.{str(modId).strip()}")



def getProfilerLogger():
    return logging.getLogger("profiler")
