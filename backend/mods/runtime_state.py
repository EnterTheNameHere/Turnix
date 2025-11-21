# backend/mods/runtime_state.py
from __future__ import annotations
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from backend.app.context import PROCESS_REGISTRY



@dataclass
class ModRuntimeSnapshot:
    allowed: set[str] = field(default_factory=set)
    backendLoaded: list[dict[str, Any]] = field(default_factory=list)
    backendFailed: list[dict[str, Any]] = field(default_factory=list)
    frontendIndex: dict[str, Any] = field(default_factory=dict)



def getModRuntimeSnapshot() -> ModRuntimeSnapshot:
    snapshot = PROCESS_REGISTRY.get("mods.snapshot")
    if isinstance(snapshot, ModRuntimeSnapshot):
        return snapshot
    empty = ModRuntimeSnapshot()
    PROCESS_REGISTRY.register("mods.snapshot", empty, overwrite=True)
    return empty



def setModRuntimeSnapshot(snapshot: ModRuntimeSnapshot) -> None:
    PROCESS_REGISTRY.register("mods.snapshot", snapshot, overwrite=True)



def setAllowedMods(modIds: Iterable[str]) -> None:
    snapshot = getModRuntimeSnapshot()
    snapshot.allowed = set(modIds)
    setModRuntimeSnapshot(snapshot)
