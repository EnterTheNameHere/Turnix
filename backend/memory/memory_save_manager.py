# backend/memory/memory_save_manager.py
from __future__ import annotations
import time
from pathlib import Path

from backend.memory.memory_layer import (
    MemoryLayer,
    DictMemoryLayer,
    CommitResult,
)
from backend.memory.memory_persistence import saveLayerToFile



class MemorySavePolicy:
    """
    Simple policy:
    - save immediately if there are any dirty keys after commit (default)
    - optional debounceMs / maxIntervalMs (per layer)
    - optional maxDirtyItems threshold
    """
    def __init__(
        self,
        *,
        debounceMs: int = 0,
        maxIntervalMs: int = 0,
        maxDirtyItems: int = 0,
    ):
        self.debounceMs = debounceMs
        self.maxIntervalMs = maxIntervalMs
        self.maxDirtyItems = maxDirtyItems



class LayerRegistration:
    def __init__(self, layer: MemoryLayer, path: Path, policy: MemorySavePolicy):
        self.layer = layer
        self.path = path
        self.policy = policy
        self.lastSavedTs: float = 0.0
        self.pendingSinceTs: float = 0.0



class MemorySaveManager:
    """
    Keeps per-layer registrations and decides when to persist.
    Usage:
      - registerLayer(...) once per layer
      - call onCommitted(commitResult) after each pipeline commit
    """
    def __init__(self, baseDir: Path | str):
        self.baseDir = Path(baseDir)
        self.byName: dict[str, LayerRegistration] = {}
    
    def registerLayer(
        self,
        layer: MemoryLayer,
        *,
        fileName: str | None = None,
        policy: MemorySavePolicy | None = None
    ) -> None:
        if isinstance(layer, DictMemoryLayer):
            name = layer.name
            filePath = self.baseDir / (fileName or f"{name}.json5")
            pol = policy or MemorySavePolicy()
            self.byName[name] = LayerRegistration(layer, filePath, pol)

    def onCommitted(self, result: CommitResult) -> None:
        # Mark pending timestamps on dirty layers
        now = time.time()
        for layerName in result.byLayer.keys():
            reg = self.byName.get(layerName)
            if not reg:
                continue
            if isinstance(reg.layer, DictMemoryLayer):
                if reg.layer.getDirtyKeys():
                    if reg.pendingSinceTs == 0.0:
                        reg.pendingSinceTs = now
        
        # Decide saves
        for reg in self.byName.values():
            self._maybeSave(reg, now)
    
    def flushLayer(self, layerName: str) -> bool:
        reg = self.byName.get(layerName)
        if not reg:
            return False
        self._saveNow(reg)
        return True
            
    def flushAll(self) -> None:
        for reg in self.byName.values():
            self._saveNow(reg)
    
    # ----- Internal -----
    
    def _maybeSave(self, reg: LayerRegistration, now: float) -> None:
        layer = reg.layer
        if not isinstance(layer, DictMemoryLayer):
            return
        dirty = layer.getDirtyKeys()
        if not dirty:
            reg.pendingSinceTs = 0.0
            return
        
        # Thresholds
        pol = reg.policy
        if pol.maxDirtyItems and len(dirty) >= pol.maxDirtyItems:
            self._saveNow(reg)
            return
        
        if pol.maxIntervalMs and (now - reg.lastSavedTs) * 1000.0 >= pol.maxIntervalMs:
            self._saveNow(reg)
            return
        
        if pol.debounceMs and reg.pendingSinceTs > 0.0:
            if (now - reg.pendingSinceTs) * 1000.0 >= pol.debounceMs:
                self._saveNow(reg)
                return
        
        # Default: save immediately if no policy set at all
        if pol.debounceMs == 0 and pol.maxIntervalMs == 0 and pol.maxDirtyItems == 0:
            self._saveNow(reg)

    def _saveNow(self, reg: LayerRegistration) -> None:
        saveLayerToFile(reg.layer, reg.path)
        reg.lastSavedTs = time.time()
        reg.pendingSinceTs = 0.0
