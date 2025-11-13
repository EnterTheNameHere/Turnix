# backend/memory/memory_persistence.py
from __future__ import annotations
from pathlib import Path
from typing import Any

import json5

from backend.memory.memory_layer import (
    DictMemoryLayer,
    MemoryLayer,
    MemoryObject,
    ReadOnlyMemoryLayer,
    TransactionalMemoryLayer,
)



def makeLayerSnapshot(layer: MemoryLayer) -> dict[str, Any]:
    """
    Turns a single layer into a serializable dict.
    Only dict-like layers are supported here. Read-only/static layers are not saved here.
    """
    # We only know how to snapshot DictMemoryLayer right now
    if not isinstance(layer, DictMemoryLayer):
        return {
            "name": layer.name,
            "kind": type(layer).__name__,
            "entries": {},
            "revision": layer.getRevision(),
        }
    
    entries: dict[str, Any] = {}
    for key, versions in layer.data.items():
        if not versions:
            continue
        obj: MemoryObject = versions[-1] # Keep only the latest
        entries[key] = {
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
        "entries": entries,
        "revision": layer.getRevision(),
    }



def saveLayerToFile(layer: MemoryLayer, path: Path | str) -> None:
    """
    Write a single DictMemoryLayer to a JSON5 file.
    """
    path = Path(path)
    data = makeLayerSnapshot(layer)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json5.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    # Mark clean after a successful write
    if isinstance(layer, DictMemoryLayer):
        layer.clearDirty()
        layer.markCleanSnapshot()



def loadLayerFromFile(
    layer: MemoryLayer,
    path: Path | str,
    *,
    missingOk: bool = True,
) -> None:
    """
    Load a file and populate existing layer by name.
    We do NOT create new layer here - we only hydrate the one we have.
    Unknown fields are ignored.
    """
    path = Path(path)
    if not path.exists():
        if missingOk:
            return
        raise FileNotFoundError(f"File '{path}' does not exist.")
    
    data = json5.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return
    
    # Only hydrate dict layers for now
    if not isinstance(layer, DictMemoryLayer):
        return
    
    entries = data.get("entries") or {}
    
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
    
    # Clear dirty and set revision if provided
    layer.clearDirty()
    rev = data.get("revision")
    if isinstance(rev, int):
        # `DictMemoryLayer` exposes getRevision() only but we
        # can bump by setting a private attr if present
        try:
            layer._revision = rev
            layer.markCleanSnapshot()
        except Exception:
            pass



def saveLayersToDir(layers: list[MemoryLayer], dirPath: Path | str) -> None:
    """
    Save each DictMemoryLayer as its own file: <dirPath>/<layer.name>.json5
    """
    base = Path(dirPath)
    base.mkdir(parents=True, exist_ok=True)
    for layer in layers:
        if isinstance(layer, TransactionalMemoryLayer) or isinstance(layer, ReadOnlyMemoryLayer):
            continue
        saveLayerToFile(layer, base / f"{layer.name}.json5")



def loadLayersFromDir(
    layers: list[MemoryLayer],
    dirPath: Path | str,
    *,
    missingOk=True
) -> None:
    """
    Load each DictMemoryLayer from <dirPath>/<layer.name>.json5 if present.
    """
    base = Path(dirPath)
    for layer in layers:
        if isinstance(layer, TransactionalMemoryLayer):
            continue
        loadLayerFromFile(layer, base / f"{layer.name}.json5", missingOk=missingOk)
