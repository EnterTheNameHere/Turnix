# backend/core/auth.py
from __future__ import annotations

from backend.rpc.models import RPCMessage



def resolvePrincipal(msg: RPCMessage) -> str:
    """Returns which principal (mod, client, etc.) originated a given RPCMessage."""
    origin = msg.origin or {}
    # Common places a mod identity might live;
    # TODO: Enforce required origin for all RPCs in future
    return str(origin.get("modId") or origin.get("principal") or "unknown")
