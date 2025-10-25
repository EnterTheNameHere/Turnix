# backend/core/dictpath.py
from __future__ import annotations
from typing import Any
from collections.abc import Mapping, MutableMapping

__all__ = ["getByPath", "setByPath", "hasPath", "deleteByPath"]



# ----------------------------------------------
#                  path parsing
# ----------------------------------------------

def _splitPathWithEscapes(path: str) -> list[str]:
    """
    Splits a dotted/slashed path where '.' and '/' are segment separators,
    and backslash '\\' escapes the next character (including separators).

    Examples:
      - a.b.c   -> ["a", "b", "c"]
      - a\\.b/c -> ["a.b", "c"]
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
            # Segment boundary
            parts.append("".join(curr))
            curr = []
            continue
        curr.append(ch)
    # If escape was left dangling (e.g., "a\\")
    if esc:
        # Signal invalid path via a ValueError. Callers that want "not found" can catch it
        raise ValueError("Path ends with a dangling escape (trailing backslash)")
    # Push last segment (even if empty - validated later)
    parts.append("".join(curr))
    return parts



def _validatePathParts(original: str, parts: list[str]) -> None:
    """
    Validates split path parts:
      • path must be non-empty and contain no empty segments (unescaped)
      • leading/trailing separators or double separators are invalid
    """
    if not isinstance(original, str) or not original:
        raise ValueError("Path must be a non-empty string")
    # Empty segment means there were consecutive separators or leading/trailing separator, e.g. a..b or a.b.
    if not parts or any(part == "" for part in parts):
        raise ValueError(f"Path '{original}' contains empty segment(s)")



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
    # Pydantic BaseModel (v2) - treat as read-only mapping via dump
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
      • if current is mapping-like and key exists → descend by key
      • else → try getattr
      • invalid path/failure → return default
    """
    try:
        parts = _splitPathWithEscapes(path)
        _validatePathParts(path, parts)
    except ValueError:
        # Invalid path is treated as "not found"
        return default
    
    current: Any = obj
    for part in parts:
        mapping = _asMapping(current)
        if mapping is not None and part in mapping:
            current = mapping[part]
            continue
        # Attribute access
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
      • If current is mapping-like and key exists → descend by key
      • If current is mapping-like but key missing:
        - If createIfMissing == True and current is a mutable mapping → create dict and descend
        - Else → raise KeyError
      • Else if attribute exists → descend via getattr
      • Else → raise AttributeError
    
    Setting the final segment:
      • If parent is a mutable mapping → parent[last] = value
      • Else if parent has attribute `last` → setattr(parent, last, value)
      • Else if parent is mapping-like, but not a mutable mapping → raise TypeError
      • Otherwise → raise AttributeError
    
    Notes:
      • Attributes are not auto-created. The attribute must exist on the object.
      • For Pydantic models, prefer writing to actual attributes; mapping dumps are read-only.
    """
    parts = _splitPathWithEscapes(path)
    _validatePathParts(path, parts)
    
    current: Any = obj
    for part in parts[:-1]:
        mapping = _asMapping(current)
        if mapping is not None:
            # Descend into mapping value if present
            if part in mapping:
                current = mapping[part]
                continue
            # If missing, allow creation on mutable mappings
            if createIfMissing and _isMutableMapping(current):
                # Create a plain dict to hold the subtree
                newChild: dict[str, Any] = {}
                current[part] = newChild
                current = newChild
                continue
            # Fallback: try attribute walk before failing (e.g., Pydantic model)
            if hasattr(current, part):
                try:
                    current = getattr(current, part)
                    continue
                except Exception as err:
                    raise AttributeError(f"Failed to access attribute '{part}' on {type(current).__name__}: {err}")
            # Key is missing and createIfMissing == False forbids us from creating it → fail
            raise KeyError(f"path segment '{part}' not found in mapping")
        # attribute walk
        if hasattr(current, part):
            try:
                current = getattr(current, part)
                continue
            except Exception as err:
                raise AttributeError(f"Failed to access attribute '{part}' on {type(current).__name__}: {err}")
        # We didn't find it
        raise AttributeError(f"path segment '{part}' not found on object of type {type(current).__name__}")
    
    last = parts[-1]
    # Write
    if _isMutableMapping(current):
        current[last] = value
        return
    
    # Mapping-like but not mutable (e.g. Pydantic dump view) → refuse
    if isinstance(current, Mapping):
        raise TypeError(f"Cannot write to '{last}' as it's read-only mapping ({type(current).__name__})")
    
    # Attribute write (do not auto-create)
    if hasattr(current, last):
        try:
            setattr(current, last, value)
            return
        except Exception as err:
            raise AttributeError(f"Failed to set attribute '{last}' on {type(current).__name__}: {err}")
    
    raise AttributeError(f"Attribute '{last}' not found on object of type {type(current).__name__}")



def hasPath(obj: Any, path: str) -> bool:
    """
    Returns True if the full path resolves (like getByPath != default),
    but without raising exceptions.
    """
    defaultNeedle = object() # Unique marker
    return getByPath(obj, path, defaultNeedle) is not defaultNeedle



def deleteByPath(obj: Any, path: str, *, pruneEmptyParents: bool = True) -> bool:
    """
    Deletes the value at `path` from `obj`. Returns True if something was removed.

    Resolution rules per hop (for all but the last segment) mirror getByPath:
      • If current is mapping-like and key exists → descend by key
      • Else if attribute exists → descend via getattr
      • Else → return False (path missing)
    
    Deleting the final segment:
      • If parent is a mutable mapping and key exists → del parent[key]
      • Else if parent has attribute `last` → delattr(parent, last)
      • Else if parent is mapping-like but not mutable → raise TypeError
      • Else → return False
    
    If pruneEmptyParents=True, empty mutable-mapping parents (only through mapping hops)
    are removed from their own parents when possible (only for mapping parents).
    """
    parts = _splitPathWithEscapes(path)
    _validatePathParts(path, parts)
    
    # Walk while keeping a stack for pruning: (parent, keyInParent)
    stack: list[tuple[Any, str]] = []
    current: Any = obj

    for part in parts[:-1]:
        mapping = _asMapping(current)
        if mapping is not None and part in mapping:
            stack.append((current, part))
            current = mapping[part]
            continue
        if hasattr(current, part):
            try:
                nxt = getattr(current, part)
            except Exception:
                return False
            # For attribute parents we still push to stack; pruning will skip non-mapping parents
            stack.append((current, part))
            current = nxt
            continue
        return False
    
    last = parts[-1]
    removed = False

    # Remove leaf
    if _isMutableMapping(current):
        if last in current:
            del current[last]
            removed = True
        else:
            return False
    elif isinstance(current, Mapping):
        # Mapping-like but immutable view
        raise TypeError(f"Cannot delete '{last}' from read-only mapping ({type(current).__name__})")
    else:
        if hasattr(current, last):
            try:
                delattr(current, last)
            except Exception as err:
                raise AttributeError(f"Failed to delete attribute '{last}' on {type(current).__name__}: {err}")
            removed = True
        else:
            return False
    
    if not removed:
        return False
    
    # Optionally prune empty mutable-mapping parents (only through mapping hops)
    if pruneEmptyParents:
        # Walk stack backwards and try to remove empty child mappings from parent mappings.
        for parent, key in reversed(stack):
            if not _isMutableMapping(parent):
                # Only prune mapping parents. Attribute parents are skipped.
                continue
            # Do not prune from the root object passed in.
            if parent is obj:
                break
            child = parent.get(key)
            if _isMutableMapping(child) and len(child) == 0:
                # Remove empty dict
                try:
                    del parent[key]
                except Exception:
                    break
            else:
                # Stop pruning once a non-empty or non-mutable-mapping encountered
                break
    
    return True
