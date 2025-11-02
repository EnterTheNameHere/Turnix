# backend/memory/memory_persistence.py
from __future__ import annotations
from pathlib import Path
from typing import Any

import json5

from backend.memory.memory_layer import (
    MemoryLayer,
    DictMemoryLayer,
    TransactionalMemoryLayer,
    MemoryObject,
)



def makeLayerSnapshot(layer: MemoryLayer) -> dict[str, Any]:
    """
    Turns a single layer into a serializable dict.
    Only dict-like layers are supported here. Read-only/static layers are assumed
    to be reconstructed from assets and thus not saved.
    """
    # We only know how to snapshot DictMemoryLayer right now
    if not isinstance(layer, DictMemoryLayer):
        return {
            "name": layer.name,
            "kind": type(layer).__name__,
            "entries": {},
        }
    
    out: dict[str, Any] = {}
    for key, versions in layer.data.items():
        if not versions:
            continue
        obj: MemoryObject = versions[-1] # Keep only the latest
        out[key] = {
            "id": obj.id,
            "path": obj.path,
            "originLayer": obj.originLayer,
            "uuidStr": obj.uuidStr,
            "version": obj.version,
            "meta": obj.meta,
            "payload": obj.payload,
        }
    
    return {
        "name": layer.name,
        "kind": "DictMemoryLayer",
        "entries": out,
    }

def snapshotLayers(layers: list[MemoryLayer]) -> dict[str, Any]:
    """
    Compose a full snapshot out of all non-txn layers.
    Result shape:

    {
        "version": 1,
        "layers": [
            {"name": "session:mainSession_...", "kind": "DictMemoryLayer", "entries": {...}}
            {"name": "runtime", "kind": "DictMemoryLayer", "entries": {...}}
            ...
        ]
    }
    """
    snapshots: list[dict[str, Any]] = []
    for layer in layers:
        # txn is never persisted
        if isinstance(layer, TransactionalMemoryLayer):
            continue
        snap = makeLayerSnapshot(layer)
        snapshots.append(snap)
    return {
        "version": 1,
        "format": "turnix.memory.layers",
        "layers": snapshots,
    }



def saveLayersToFile(layers: list[MemoryLayer], path: Path | str) -> None:
    """
    Write all DictMemoryLayer instances to a JSON file.
    Creates a parent directory if it doesn't exist yet.
    """
    path = Path(path)
    data = snapshotLayers(layers)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json5.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")



def loadLayersFromFile(
    layers: list[MemoryLayer],
    path: Path | str,
    *,
    missingOk: bool = True,
) -> None:
    """
    Load a file and populate existing layers by name.
    We do NOT create new layers here - we only hydrate the ones we already have.
    Unknown layers in the file are ignored.
    """
    path = Path(path)
    if not path.exists():
        if missingOk:
            return
        raise FileNotFoundError(f"File '{path}' does not exist.")
    
    data = json5.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return
    
    layerSnapshots = data.get("layers")
    if not isinstance(layerSnapshots, list):
        return
    
    # Map for quick lookup
    byName: dict[str, MemoryLayer] = {layer.name: layer for layer in layers}

    for layerSnapshot in layerSnapshots:
        name = layerSnapshot.get("name")
        if not name or name not in byName:
            continue
        
        layer = byName[name]
        
        # Only hydrate dict layers for now
        if isinstance(layer, DictMemoryLayer):
            entries = layerSnapshot.get("entries") or {}
            
            # Overwrite existing content
            layer.data.clear()
            
            for key, entry in entries.items():
                obj = MemoryObject(
                    id=entry.get("id", key),
                    payload=entry.get("payload"),
                    path=entry.get("path", key),
                    originLayer=entry.get("originLayer", layer.name),
                    uuidStr=entry.get("uuidStr", ""),
                    version=entry.get("version", 1),
                    meta=entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
                )
                layer.data[key] = [obj]
        else:
            # Other layer kind - ignore for now
            continue
