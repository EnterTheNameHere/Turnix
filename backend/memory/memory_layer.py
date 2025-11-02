# backend/memory/memory_layer.py
from __future__ import annotations

from backend.core.ids import uuidv7



class MemoryObject:
    """
    Base for anything that lives in memory.
    Carries origin so we can save(obj) without restating path.
    """
    def __init__(
        self,
        *,
        id: str,
        payload,
        path: str = "",
        originLayer: str = "",
        uuidStr: str = "",
        version: int = 1,
        meta: dict | None = None,
    ):
        self.id = id
        self.payload = payload
        self.path = path               # e.g. "session.chatHistory.42"
        self.originLayer = originLayer # e.g. "session", "scoped", ...
        self.uuidStr = uuidStr or uuidv7()
        self.version = version
        self.meta = meta if isinstance(meta, dict) else {}
    
    def bumpVersion(self):
        self.version += 1



class QueryItem(MemoryObject):
    """
    Promptable unit. Mods create these, pipeline renders them.
    """
    def __init__(
        self,
        *,
        id: str,
        kind: str,
        payload,
        path: str = "",
        originLayer: str = "",
        uuidStr: str = "",
        version: int = 1,
        meta: dict | None = None
    ):
        super().__init__(
            id=id,
            payload=payload,
            path=path,
            originLayer=originLayer,
            uuidStr=uuidStr,
            version=version,
            meta=meta
        )
        self.kind = kind # "userMessage", "assistantMessage", "system", "memory", ...



class MemoryLayer:
    name: str

    def get(self, key: str):
        raise NotImplementedError
    
    def set(self, key: str, value):
        raise NotImplementedError
    
    def delete(self, key: str):
        raise NotImplementedError
    
    def canWrite(self) -> bool:
        return True



class ReadOnlyMemoryLayer(MemoryLayer):
    def __init__(self, name: str, data: dict):
        self.name = name
        self.data = data

    def get(self, key: str):
        return self.data.get(key)
    
    def set(self, key: str, value):
        raise RuntimeError("Read-only layer")
    
    def delete(self, key: str):
        raise RuntimeError("Read-only layer")
    
    def canWrite(self) -> bool:
        return False



class DictMemoryLayer(MemoryLayer):
    """
    Generic mutable layer: session, runtime, party, persistent, whatever.
    """
    def __init__(self, name: str, maxVersionsToKeep: int = 3):
        self.name = name
        self.maxVersionsToKeep = maxVersionsToKeep
        self.data: dict[str, list[MemoryObject]] = {}
    
    def get(self, key: str):
        versions = self.data.get(key)
        if not versions:
            return None
        return versions[-1]
    
    def set(self, key: str, value: MemoryObject):
        versions = self.data.get(key)
        if not versions:
            versions = []
            self.data[key] = versions
        versions.append(value)
        if len(versions) > self.maxVersionsToKeep:
            # Keep last N
            self.data[key] = versions[-self.maxVersionsToKeep :]

    def delete(self, key: str):
        self.data.pop(key, None)



class TransactionalMemoryLayer(MemoryLayer):
    """
    Top layer: per-pipeline-run.
    Stores staged changes as (key, obj).
    """
    def __init__(self, name: str = "txn") -> None:
        self.name = name
        self.staged: dict[str, MemoryObject] = {}
        self.changes: list[tuple[str, MemoryObject | None]] = []
        self.allowWrites = True
    
    def get(self, key: str):
        return self.staged.get(key)

    def set(self, key: str, value: MemoryObject):
        if not self.allowWrites:
            raise RuntimeError("Writes are not allowed in this transaction phase")
        self.staged[key] = value
        self.changes.append((key, value))
    
    def delete(self, key: str):
        if key in self.staged:
            del self.staged[key]
        self.changes.append((key, None))

    def canWrite(self) -> bool:
        return self.allowWrites
    
    def clear(self):
        self.staged.clear()
        self.changes.clear()



# Aliases for Turnix contexts
SessionMemoryLayer = DictMemoryLayer
ScopedMemoryLayer = DictMemoryLayer
RuntimeMemoryLayer = DictMemoryLayer
ViewMemoryLayer = DictMemoryLayer
PipelineRunMemoryLayer = TransactionalMemoryLayer



# ----------------------------------------------
# Resolver
# ----------------------------------------------

class ResolvedKey:
    def __init__(self, explicitLayer: str, key: str):
        self.explicitLayer = explicitLayer
        self.key = key



class MemoryResolver:
    """
    Maps prefixes like "session.", "scoped.", "party." to actual layer names.
    """
    def __init__(self, nsToLayerName: dict[str, str]):
        self.nsToLayerName = nsToLayerName
    
    def normalize(self, key: str) -> ResolvedKey:
        parts = key.split(".")
        if len(parts) > 1 and parts[0] in self.nsToLayerName:
            return ResolvedKey(self.nsToLayerName[parts[0]], ".".join(parts[1:]))
        return ResolvedKey("", key)
    
    def stripNamespace(self, key: str) -> str:
        parts = key.split(".")
        if len(parts) > 1 and parts[0] in self.nsToLayerName:
            return ".".join(parts[1:])
        return key
    
    def pickTargetLayer(self, key: str, layers: list[MemoryLayer]) -> MemoryLayer:
        normKey = self.normalize(key)
        if normKey.explicitLayer:
            for layer in layers:
                if layer.name == normKey.explicitLayer:
                    return layer
            raise RuntimeError(f"No layer named '{normKey.explicitLayer}'")

        for layer in layers:
            if layer.canWrite():
                return layer
        
        raise RuntimeError("No writeable layer found")


# ----------------------------------------------
# Accessor
# ----------------------------------------------

class LayeredMemory:
    def __init__(self, layers: list[MemoryLayer], resolver: MemoryResolver, txn: TransactionalMemoryLayer):
        self.layers = layers
        self.resolver = resolver
        self.txn = txn
    
    def get(self, key: str) -> MemoryObject | None:
        normKey = self.resolver.normalize(key)
        if normKey.explicitLayer:
            layer = self._getLayerByName(normKey.explicitLayer)
            obj = layer.get(normKey.key)
            if obj is None:
                return None
            return self._ensureOrigin(obj, layer.name, key)
        # Implicit: walk
        for layer in self.layers:
            obj = layer.get(normKey.key)
            if obj is not None:
                return self._ensureOrigin(obj, layer.name, key)
        return None
    
    def save(self, obj: MemoryObject):
        # Transactional save
        key = obj.path
        if not key:
            # Derive from origin or make txn key
            if obj.originLayer:
                key = f"{obj.originLayer}.{obj.id or obj.uuidStr}"
            else:
                key = f"txn.{obj.uuidStr}"
            obj.path = key
        self.txn.set(key, obj)
    
    def savePersistent(self, obj: MemoryObject):
        key = obj.path
        if not key:
            if obj.originLayer:
                key = f"{obj.originLayer}.{obj.id or obj.uuidStr}"
            else:
                key = f"session.{obj.id or obj.uuidStr}"
            obj.path = key # Keep object in sync
        target = self.resolver.pickTargetLayer(key, self.layers)
        # If obj has no origin yet
        if not obj.originLayer:
            obj.originLayer = target.name
        target.set(self.resolver.stripNamespace(key), obj)
    
    def _getLayerByName(self, name: str) -> MemoryLayer:
        for layer in self.layers:
            if layer.name == name:
                return layer
        raise RuntimeError(f"Layer '{name}' not found")
    
    def _ensureOrigin(self, obj: MemoryObject, layerName: str, path: str) -> MemoryObject:
        if not obj.originLayer:
            obj.originLayer = layerName
        if not obj.path:
            obj.path = path
        return obj


# ----------------------------------------------
# Propagator
# ----------------------------------------------

class MemoryPropagator:
    def __init__(self, resolver: MemoryResolver):
        self.resolver = resolver

    def commit(self, layers: list[MemoryLayer]) -> int:
        if not layers or len(layers) < 2:
            raise ValueError("commit() expects at least two layers: txn at index 0 and at least one real layer after it.")
        
        txn = layers[0]
        if not isinstance(txn, TransactionalMemoryLayer):
            raise ValueError("commit() expects layers[0] to be TransactionalMemoryLayer")

        if not txn.changes:
            return 0
        
        numOfChanges = 0
        for key, obj in txn.changes:
            # Skip txn layer at index 0 – propagate only to real/persistent layers
            target = self.resolver.pickTargetLayer(key, layers[1:])
            if obj is None:
                # Delete
                target.delete(self.resolver.stripNamespace(key))
            else:
                # Normal set
                target.set(self.resolver.stripNamespace(key), obj)
            numOfChanges += 1
        
        txn.clear()
        return numOfChanges
    
    def rollback(self, layers: list[MemoryLayer]):
        if not layers or not isinstance(layers[0], TransactionalMemoryLayer):
            raise ValueError("rollback() expects txn layer at index 0")
        layers[0].clear()
