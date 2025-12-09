# backend/memory/memory_layer.py
from __future__ import annotations

from collections.abc import Iterable

from backend.app.globals import getTracer
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
    """
    Minimal interface. Layers do not know about other layers.
    """
    name: str

    def get(self, key: str) -> MemoryObject | None:
        raise NotImplementedError
    
    def set(self, key: str, value: MemoryObject) -> None:
        raise NotImplementedError
    
    def delete(self, key: str) -> None:
        raise NotImplementedError
    
    def canWrite(self) -> bool:
        return True
    
    # ----- Persistence helpers / Telemetry -----
    def getDirtyKeys(self) -> set[str]:
        return set()
    
    def clearDirty(self) -> None:
        pass
    
    def getRevision(self) -> int:
        return 0
    
    def markCleanSnapshot(self) -> None:
        """
        Called after successful save. Can store counters for policy decisions.
        """
        pass



class ReadOnlyMemoryLayer(MemoryLayer):
    def __init__(self, name: str, data: dict):
        self.name = name
        self.data = data

    def get(self, key: str) -> MemoryObject | None:
        return self.data.get(key)
    
    def set(self, key: str, value: MemoryObject) -> None:
        raise RuntimeError("Read-only layer")
    
    def delete(self, key: str) -> None:
        raise RuntimeError("Read-only layer")
    
    def canWrite(self) -> bool:
        return False



class DictMemoryLayer(MemoryLayer):
    """
    Generic mutable layer: session, runtime, party, persistent, whatever.
    - Keeps last N versions per key.
    - Tracks dirty keys and monotonically increasing revision for change detection.
    """
    def __init__(self, name: str, maxVersionsToKeep: int = 3):
        self.name = name
        self.maxVersionsToKeep = maxVersionsToKeep
        self.data: dict[str, list[MemoryObject]] = {}
        self._dirty: set[str] = set()
        self._revision: int = 0
        self._lastSavedRevision: int = 0
    
    def get(self, key: str) -> MemoryObject | None:
        versions = self.data.get(key)
        if not versions:
            return None
        return versions[-1]
    
    def set(self, key: str, value: MemoryObject) -> None:
        versions = self.data.get(key)
        if not versions:
            versions = []
            self.data[key] = versions
        versions.append(value)
        if len(versions) > self.maxVersionsToKeep:
            # Keep last N
            self.data[key] = versions[-self.maxVersionsToKeep :]
        self._dirty.add(key)
        self._revision += 1

    def setMany(self, items: Iterable[tuple[str, MemoryObject]]) -> None:
        for key, value in items:
            self.set(key, value)
    
    def delete(self, key: str) -> None:
        if key in self.data:
            del self.data[key]
        self._dirty.add(key)
        self._revision += 1
        
    def getDirtyKeys(self) -> set[str]:
        return set(self._dirty)
    
    def clearDirty(self) -> None:
        self._dirty.clear()

    def getRevision(self) -> int:
        return self._revision

    def markCleanSnapshot(self) -> None:
        self._lastSavedRevision = self._revision
        


class TransactionalMemoryLayer(MemoryLayer):
    """
    Top layer: per-pipeline-run.
    Stores staged changes as (key, obj). Not persisted directly.
    """
    def __init__(self, name: str = "txn") -> None:
        self.name = name
        self.staged: dict[str, MemoryObject] = {}
        self.changes: list[tuple[str, MemoryObject | None]] = []
        self.allowWrites = True
    
    def get(self, key: str) -> MemoryObject | None:
        return self.staged.get(key)

    def set(self, key: str, value: MemoryObject) -> None:
        if not self.allowWrites:
            raise RuntimeError("Writes are not allowed in this transaction phase")
        self.staged[key] = value
        self.changes.append((key, value))
    
    def delete(self, key: str) -> None:
        if key in self.staged:
            del self.staged[key]
        self.changes.append((key, None))

    def canWrite(self) -> bool:
        return self.allowWrites
    
    def clear(self) -> None:
        self.staged.clear()
        self.changes.clear()



# Aliases for Turnix contexts
SessionMemoryLayer = DictMemoryLayer
ScopedMemoryLayer = DictMemoryLayer
AppInstanceMemoryLayer = DictMemoryLayer
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
    
    def getByPath(self, path: str) -> MemoryObject | None:
        """
        Fetch by fully qualified or implicitly resolved path.
        Alias for get().
        """
        return self.get(path)
    
    def getByUuid(self, uuidStr: str, includeTxn: bool = False) -> tuple[str, MemoryObject] | None:
        """
        Linear scan for now. Returns (layerName, obj) or None.
        """
        for layer in self.layers:
            if isinstance(layer, DictMemoryLayer):
                for _key, versions in layer.data.items():
                    if versions and versions[-1].uuidStr == uuidStr:
                        return (layer.name, versions[-1])
        if includeTxn and isinstance(self.txn, TransactionalMemoryLayer):
            for _key, obj in self.txn.staged.items():
                if obj.uuidStr == uuidStr:
                    return (self.txn.name, obj)
        return None
    
    def save(self, obj: MemoryObject) -> str:
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
        return key
    
    def savePersistent(self, obj: MemoryObject) -> str:
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
        cleanKey = self.resolver.stripNamespace(key)
        target.set(cleanKey, obj)
        return key
    
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

class CommitResult:
    """
    Summary of what changed, grouped per target layer.
    """
    def __init__(self):
        self.byLayer: dict[str, dict[str, int]] = {} # {layer: {"set": n, "del": n}}
    
    def add(self, layerName: str, op: str) -> None:
        entries = self.byLayer.get(layerName) or {"set": 0, "del": 0}
        entries["set" if op == "set" else "del"] += 1
        self.byLayer[layerName] = entries
    
    def isEmpty(self) -> bool:
        return not self.byLayer



class MemoryPropagator:
    def __init__(self, resolver: MemoryResolver):
        self.resolver = resolver

    def commit(self, layers: list[MemoryLayer]) -> CommitResult:
        tracer = getTracer()
        span = None
        try:
            span = tracer.startSpan(
                "memory.commit",
                attrs={"layerCount": len(layers) if layers is not None else 0},
                tags=["memory"],
            )
            tracer.traceEvent(
                "memory.commit.start",
                attrs={"layerCount": len(layers) if layers is not None else 0},
                tags=["memory"],
                span=span,
            )
        except Exception:
            span = None
        
        try:
            if not layers or len(layers) < 2:
                raise ValueError(
                    "commit() expects at least two layers: txn at index 0 and at least one real layer after it."
                )
            
            txn = layers[0]
            if not isinstance(txn, TransactionalMemoryLayer):
                raise ValueError("commit() expects layers[0] to be TransactionalMemoryLayer")

            result = CommitResult()
            if not txn.changes:
                if span is not None:
                    attrs = {
                        "empty": True,
                        "totalChanges": 0,
                        "layersTouched": 0,
                    }
                    tracer.traceEvent(
                        "memory.commit.end",
                        attrs=attrs,
                        tags=["memory"],
                        span=span,
                    )
                    tracer.endSpan(
                        span,
                        status="ok",
                        level="debug",
                        tags=["memory"],
                        attrs=attrs,
                    )
                return result

            totalChanges = len(txn.changes)
        
            for key, obj in txn.changes:
                # Skip txn layer at index 0 – propagate only to real/persistent layers
                target = self.resolver.pickTargetLayer(key, layers[1:])
                cleanKey = self.resolver.stripNamespace(key)
                if obj is None:
                    # Delete
                    target.delete(cleanKey)
                    result.add(target.name, "del")
                else:
                    # Normal set
                    target.set(cleanKey, obj)
                    result.add(target.name, "set")
        
            txn.clear()
            
            if span is not None:
                attrs = {
                    "empty": False,
                    "totalChanges": totalChanges,
                    "layersTouched": len(result.byLayer),
                }
                tracer.traceEvent(
                    "memory.commit.end",
                    attrs=attrs,
                    level="info",
                    tags=["memory"],
                    span=span,
                )
                tracer.endSpan(
                    span,
                    status="ok",
                    tags=["memory"],
                    attrs=attrs,
                )
            return result
        except Exception as err:
            if span is not None:
                try:
                    attrs = {"error": str(err)}
                    tracer.traceEvent(
                        "memory.commit.error",
                        attrs=attrs,
                        level="error",
                        tags=["memory"],
                        span=span,
                    )
                    tracer.endSpan(
                        span,
                        status="error",
                        level="error",
                        tags=["memory"],
                        attrs=attrs,
                    )
                except Exception:
                    pass
            raise
    
    def rollback(self, layers: list[MemoryLayer]) -> None:
        tracer = getTracer()
        span = None
        try:
            span = tracer.startSpan(
                "memory.rollback",
                attrs={"layerCount": len(layers) if layers is not None else 0},
                tags=["memory"],
            )
            tracer.traceEvent(
                "memory.rollback.start",
                attrs={"layerCount": len(layers) if layers is not None else 0},
                tags=["memory"],
                span=span,
            )
        except Exception:
            span = None
        
        try:
            if not layers or not isinstance(layers[0], TransactionalMemoryLayer):
                raise ValueError("rollback() expects txn layer at index 0")
            layers[0].clear()
            
            if span is not None:
                attrs = {"ok": True}
                tracer.traceEvent(
                    "memory.rollback.end",
                    attrs=attrs,
                    level="debug",
                    tags=["memory"],
                    span=span,
                )
                tracer.endSpan(
                    span,
                    status="ok",
                    level="debug",
                    tags=["memory"],
                    attrs=attrs,
                )
        except Exception as err:
            if span is not None:
                try:
                    attrs = {"error": str(err)}
                    tracer.traceEvent(
                        "memory.rollback.error",
                        attrs=attrs,
                        level="error",
                        tags=["memory"],
                        span=span,
                    )
                    tracer.endSpan(
                        span,
                        status="error",
                        level="error",
                        tags=["memory"],
                        attrs=attrs,
                    )
                except Exception:
                    pass
            raise
