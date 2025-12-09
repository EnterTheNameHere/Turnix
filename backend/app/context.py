# backend/app/context.py
from __future__ import annotations

from typing import Any, TypeVar, cast

T = TypeVar("T")



class _ProcessContext:
    def __init__(self) -> None:
        self._services: dict[str, Any] = {}
    
    def register(self, name: str, service: Any, *, overwrite: bool = False) -> None:
        if not overwrite and name in self._services:
            raise ValueError(f"Service '{name}' already registered")
        self._services[name] = service

    def get(self, name: str) -> Any | None:
        return self._services.get(name)
    
    def require(self, name: str, /, tp: type[T] | None = None) -> T:
        val = self._services.get(name)
        if val is None:
            raise RuntimeError(f"Required service '{name}' not found")
        if tp is not None and not isinstance(val, tp):
            raise TypeError(f"Service '{name}' is not of expected type {tp.__name__}")
        return cast(T, val)

# Single instance
PROCESS_REGISTRY = _ProcessContext()
