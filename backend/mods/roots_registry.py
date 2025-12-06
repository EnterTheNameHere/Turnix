# backend/mods/roots_registry.py
from __future__ import annotations
from pathlib import Path
from collections.abc import Iterable



# mountId -> tuple of root Paths (order matters)
_REGISTRY: dict[str, tuple[Path, ...]] = {}



def registerRoots(mountId: str, roots: Iterable[Path]) -> None:
    """
    Registers base roots for a given mount/view id.
    Paths are normalized to absolute, non-strict resolved paths.
    """
    _REGISTRY[mountId] = tuple(Path(root).resolve(strict=False) for root in roots)



def getRoots(mountId: str) -> tuple[Path, ...] | None:
    """
    Returns the registered roots for this mount/view id, or None if not registered.
    """
    return _REGISTRY.get(mountId)
