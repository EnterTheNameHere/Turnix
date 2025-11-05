# backend/mods/roots_registry.py
from __future__ import annotations
from pathlib import Path
from collections.abc import Iterable



# mountId -> tuple of root Paths (order matters)
_REGISTRY: dict[str, tuple[Path, ...]] = {}



def registerRoots(mountId: str, roots: Iterable[Path]) -> None:
    _REGISTRY[mountId] = tuple(Path(root).resolve() for root in roots)

def getRoots(mountId: str) -> tuple[Path, ...] | None:
    return _REGISTRY.get(mountId)
