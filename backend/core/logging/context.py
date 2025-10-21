# backend/core/logging/context.py
from __future__ import annotations
import contextvars

# All log context lives here. Enrich this from RPC receive path.
_logContextVar: contextvars.ContextVar[dict[str, object] | None] = contextvars.ContextVar("turnix.logctx", default=None)

def setLogContext(**kvs):
    """Set or update per-log context values (requestId, viewId, modId, etc.)."""
    current = dict(_logContextVar.get() or {}) # use copy
    for key, value in kvs.items():
        if value is not None:
            current[key] = value
    _logContextVar.set(current)

def clearLogContext():
    """Clear context after a request/frame is fully handled."""
    _logContextVar.set(None)

def getLogContext():
    """Return current content dict or None."""
    return _logContextVar.get()
