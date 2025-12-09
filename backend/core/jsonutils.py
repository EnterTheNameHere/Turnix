# backend/core/jsonutils.py
from __future__ import annotations

import base64
import json
import traceback
from collections import deque
from collections.abc import Mapping, Iterable
from dataclasses import is_dataclass, asdict
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from backend.rpc.models import RPCMessage

__all__ = ["safeJsonDumps", "serializeError", "tryJSONify"]



# ------------------------------------------------
#                JSON serialization
# ------------------------------------------------

def safeJsonDumps(obj: object | RPCMessage) -> str:
    """
    Serializes an object or RPCMessage to a compact JSON string.
    Uses deterministic separators (",", ":") and disallows NaN/infinity.
    Ensures ASCII is preserved, but UTF-8 characters are kept as-is.
    If direct JSON encoding fails, falls back to tryJSONify (circular/depth-safe) and retries.
    """
    payload: Any
    if isinstance(obj, RPCMessage):
        payload = obj.model_dump(by_alias=True, exclude_unset=True)
    else:
        payload = obj
    
    try:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except Exception:
        # Hardened fallback
        safePayload = tryJSONify(payload, _maxDepth=None)
        return json.dumps(safePayload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))



# ------------------------------------------------
#              Error / Exception helpers
# ------------------------------------------------

def serializeError(err: Any) -> dict[str, Any]:
    """
    Converts Exception or arbitrary object into a JSON-serializable dict.
    
    Examples:
        ValueError("bad") -> {"type": "ValueError", "message": "bad", "stack": "..."}
        "error text"      -> {"message": "error text"}
        None              -> {}
    """
    from backend.app.globals import config
    if err is None:
        return {}
    
    if isinstance(err, str):
        return {"message": err}
    
    if isinstance(err, BaseException):
        data = {
            "type": err.__class__.__name__,
            "name": err.__class__.__name__,
            "message": str(err),
            "args": [repr(arg) for arg in getattr(err, "args", [])],
        }
        
        traceBack = getattr(err, "__traceback__", None)
        if traceBack:
            que: deque[str] = deque()
            total = 0
            limit = int(config("debug.tracebackCharLimit", 4000))
            truncated = False

            for part in traceback.format_tb(traceBack):
                que.append(part)
                total += len(part)
                while total > limit and que:
                    left = que.popleft()
                    total -= len(left)
                    truncated = True
                
            text = "".join(que)
            if truncated:
                text += "[TRUNCATED]"
            data["stack"] = text
        
        return data
    
    # Try to dump as-is, and if that fails, use repr
    try:
        json.dumps(err)
        return err
    except Exception:
        return {"type": type(err).__name__, "repr": repr(err)}



# ------------------------------------------------
#              Generic JSON safety
# ------------------------------------------------

def tryJSONify(obj: Any, *, _seen: set[int] | None = None, _depth: int = 0, _maxDepth: int | None = 10) -> Any:
    """
    Attempts to make any object JSON-serializable.
    
    Rules:
      • Basic scalars (None, bool, int, float, str) are preserved.
      • Exceptions → serializeError().
      • bytes/bytearray/memoryview → base64 {"__b64__":"..."}.
      • date/datetime → ISO8601 string.
      • Path → string path.
      • sets/tuples/iterables → list.
      • Mappings → dict with str keys.
      • fallback → repr(obj)
    
    Recursion guards:
      • _seen prevents cycles.
      • _maxDepth stops deep recursion; when explicitly set to None, recursion guard is turned off.
    """
    if _seen is None:
        _seen = set()
    if _maxDepth is not None and not isinstance(_maxDepth, int):
        _maxDepth = 10

    oid = id(obj)
    if oid in _seen:
        return f"<circular_ref {type(obj).__name__}>"
    if isinstance(_maxDepth, int) and _depth > _maxDepth:
        return f"<max_depth_exceeded {type(obj).__name__}>"
    _seen.add(oid)

    # Primitives
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    
    # Exceptions
    if isinstance(obj, BaseException):
        return serializeError(obj)

    # bytes/bytearray/memoryview
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return {"__b64__": base64.b64encode(bytes(obj)).decode("ascii")}
    
    # datetime/date
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()

    # Enum
    if isinstance(obj, Enum):
        return tryJSONify(obj.value, _seen=_seen, _depth=_depth + 1, _maxDepth=_maxDepth)
    
    # Dataclass instance
    if is_dataclass(obj) and not isinstance(obj, type):
        return tryJSONify(asdict(obj), _seen=_seen, _depth=_depth + 1, _maxDepth=_maxDepth)

    # Dataclass class
    if isinstance(obj, type) and is_dataclass(obj):
        return obj.__name__ or repr(obj)

    # Path
    if isinstance(obj, Path):
        return str(obj)
    
    # sets/frozensets/tuples
    if isinstance(obj, (set, frozenset, tuple)):
        return [tryJSONify(value, _seen=_seen, _depth=_depth + 1, _maxDepth=_maxDepth) for value in obj]
    
    # Mappings
    if isinstance(obj, Mapping):
        return {
            str(key): tryJSONify(value, _seen=_seen, _depth=_depth+1, _maxDepth=_maxDepth) for key, value in obj.items()
        }

    # Iterables which are not handled above
    if isinstance(obj, Iterable):
        return [tryJSONify(value, _seen=_seen, _depth=_depth + 1, _maxDepth=_maxDepth) for value in obj]
    
    # Last-ditch representation (avoid raising during logging)
    return repr(obj)
