# backend/core/dictpath.py
from __future__ import annotations
from typing import Any, Mapping

__all__ = ["getByPath"]



def _splitPathWithEscapes(path: str) -> list[str]:
    parts: list[str] = []
    curr: list[str] = []
    esc = False
    for ch in path:
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



def _asMapping(obj: Any) -> Mapping[str, Any] | None:
    if isinstance(obj, Mapping):
        return obj
    # Pydantic BaseModel
    if hasattr(obj, "model_dump"):
        return obj.model_dump(by_alias=True, exclude_unset=True)
    return None



def getByPath(obj: Any, path: str) -> Any:
    """
    Returns the value at `path` from `obj` if `obj` is a mapping-like structure.
    Dotted/slashed segments are supported; '\\' escapes separators.
    Returns None when the path can't be resolved.
    """
    mapping = _asMapping(obj)
    if mapping is None or not path:
        return None
    val: Any = mapping
    if "." not in path and "/" not in path:
        return mapping.get(path)
    for part in _splitPathWithEscapes(path):
        mm = _asMapping(val)
        if mm is None or part not in mm:
            return None
        val = mm[part]
    return val
