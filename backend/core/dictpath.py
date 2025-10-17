# backend/core/dictpath.py
from __future__ import annotations
from typing import Any
from collections.abc import Mapping, MutableMapping

__all__ = ["getByPath", "setByPath", "hasPath"]



# ----------------------------------------------
#                  path parsing
# ----------------------------------------------

def _splitPathWithEscapes(path: str) -> list[str]:
    """
    Splits a dotted/slashed path where '.' and '/' are segment separators,
    and backslash '\\' escapes the next character (including separators).
    """
    parts: list[str] = []
    curr: list[str] = []
    esc = False
    for ch in path or "":
        if esc:
            curr.append(ch)
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch in (".", "/"):
            if curr:
                parts.append("".join(curr))
                curr = []
            continue
        curr.append(ch)
    if curr:
        parts.append("".join(curr))
    return parts



# ----------------------------------------------
#                Mapping adapters
# ----------------------------------------------

def _asMapping(obj: Any) -> Mapping[str, Any] | None:
    """
    Best-effort mapping view:
      • dict → itself
      • Pydantic BaseModel → model_dump(by_alias=True, exclude_unset=True)
      • any other Mapping → itself
      • anything else → None
    """
    if isinstance(obj, Mapping):
        return obj
    # Pydantic BaseModel
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(by_alias=True, exclude_unset=True)
        except Exception:
            return None
    return None



def _isMutableMapping(obj: Any) -> bool:
    return isinstance(obj, MutableMapping)



# ----------------------------------------------
#                   Public API
# ----------------------------------------------

def getByPath(obj: Any, path: str, default: Any | None = None) -> Any:
    """
    Returns the value at `path` from `obj` if reachable. When the chain
    cannot be resolved, returns `default`.
    Resolution rules per hop:
      • if current is mapping-like and key exists → descent by key
      • else → try getattr
      • on failure → return default
    """
    if not path:
        return default
    
    current: Any = obj
    parts = _splitPathWithEscapes(path)
    for part in parts:
        mapping = _asMapping(current)
        if mapping is not None and part in mapping:
            current = mapping[part]
            continue
        # attribute access
        if hasattr(current, part):
            try:
                current = getattr(current, part)
                continue
            except Exception:
                return default
        return default
    return current



def setByPath(obj: Any, path: str, value: Any, *, createIfMissing: bool = False) -> None:
    """
    Sets the value at `path` on `obj`.

    Resolution rules per hop (for all but the last segment):
      • If current is mapping-like and key exists → descent by key
      • If current is mapping-like but key missing:
        - If createIfMissing == True and current is a mutable dict → create dict and descent
        - Else → raise KeyError
      • Else if attribute exists → descent via getattr
      • Else → raise AttributeError
    
    Setting the final segment:
      • If parent is a mutable dict → parent[last] = value
      • Else if parent has attribute `last` → setattr(parent, last, value)
      • Else if parent is a mapping-like, but not a mutable dict → raise TypeError
      • Otherwise → raise AttributeError
    
    Notes:
      • We intentionally DO NOT auto-create attributes on arbitrary objects.
      • For Pydantic models, prefer writing to actual attributes; model_dump views
        are read-only, while setting through mapping requires the real object/attr.
    """
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    
    parts = _splitPathWithEscapes(path)
    if not parts:
        raise ValueError("path must be a valid non-empty string")
    
    current: Any = obj
    for part in parts[:-1]:
        mapping = _asMapping(current)
        if mapping is not None:
            # Descent
            if part in mapping:
                current = mapping[part]
                continue
            # Key missing
            if createIfMissing and _isMutableMapping(current):
                # Create a plain dict to hold the subtree
                newChild: dict[str, Any] = {}
                current[part] = newChild
                current = newChild
                continue
            # Key missing and createIfMissing == False; fail
            raise KeyError(f"path segment '{part}' not found in mapping")
        # attribute walk
        if hasattr(current, part):
            current = getattr(current, part)
            continue
        # We didn't found it
        raise AttributeError(f"path segment '{part}' not found on object of type {type(current).__name__}")
    
    last = parts[-1]
    # Write
    if _isMutableMapping(current):
        current[last] = value
        return
    
    # If it's mapping-like, but not mutable (e.g. Pydantic dump view), refuse
    if isinstance(current, Mapping):
        raise TypeError(f"cannot write to '{last}' as it's read-only mapping ({type(current).__name__})")
    
    # attribute write
    if hasattr(current, last) or not isinstance(current, Mapping):
        try:
            setattr(current, last, value)
            return
        except Exception as err:
            raise AttributeError(f"failed to set attribute '{last}' on {type(current).__name__}: {err}")
    
    raise AttributeError(f"Cannot set '{last}' on object of type {type(current).__name__}")



def hasPath(obj: Any, path: str) -> bool:
    """
    Returns True if the full path resolves (like getByPath != default),
    but without raising exceptions.
    """
    defaultNeedle: tuple[object] = (object(),) # unique marker
    return getByPath(obj, path, defaultNeedle) is not defaultNeedle
