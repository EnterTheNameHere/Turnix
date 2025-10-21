# backend/core/logging/__init__.py
from __future__ import annotations

from .context import setLogContext, clearLogContext, getLogContext
from .handlers import getJSLogHandler, setJSLoggingReady
from .setup import configureLogging
from .util import getLogger, getModLogger, getProfilerLogger

__all__ = [
    "configureLogging",
    "getLogger",
    "getModLogger",
    "getProfilerLogger",
    "getJSLogHandler",
    "setJSLoggingReady",
    "setLogContext",
    "clearLogContext",
    "getLogContext",
]
