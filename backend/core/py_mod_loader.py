from __future__ import annotations
import asyncio, importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Callable

from fastapi import HTTPException

from backend.server import scanMods, findManifestPath
from core.logger import getModLogger
logger = getModLogger("py_mod_loader")

@dataclass
class LoadedPyMod:
    modId: str
    name: str
    version: str
    module: Any
    entryPath: Path

class PyModContext:
    def __init__(self, services: dict[str, Any], settings: dict[str, Any]):
        self._services = services
        self.settings = settings

    def registerService(self, name: str, service: Any):
        if not name or not isinstance(name, str):
            raise ValueError("Service name must be a non-empty string")
        self._services[name] = service
        logger.info(f"Registered backend service '{name}' via Python mod")

def _quickImport(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

async def loadPythonMods(*, settings: dict[str, Any]) -> tuple[list[LoadedPyMod], list[dict[str, Any]], dict[str, Any]]:
    """
    Returns (loaded, failed, services) where:
      - loaded: list of LoadedPyMod
      - failed: list of {id, reason, stack?}
      - services: mapping registered by mods via ctx.registerService(name, instance)
    """
    discovered = scanMods()
    enabled = []
    for _modId, (_root, moddir, manifest, _fname) in discovered.items():
        entryPath = (moddir / manifest.entry)
        # Minimal: select only Python entries (.py)
        if not str(entryPath).endswith(".py"):
            continue
        enabled.append((manifest, moddir, entryPath))
    
    # NOTE: Python has "lower order" to make it easier for now
    services: dict[str, Any] = {}
    loaded: list[LoadedPyMod] = []
    failed: list[dict[str, Any]] = []

    for manifest, moddir, entryPath in sorted(enabled, key=lambda t: (t[0].id, t[0].version)):
        try:
            if not entryPath.exists():
                raise HTTPException(404, f"'{manifest.id}' - entry file not found: '{entryPath}'")

            module = _quickImport(entryPath.resolve())
            onLoad = getattr(module, "onLoad", None)

            ctx = PyModContext(services=services, settings=settings)
            if asyncio.iscoroutinefunction(onLoad):
                await onLoad(ctx) # async
            elif callable(onLoad):
                onLoad(ctx)       # sync
            else:
                logger.info(f"Python mod '{manifest.id}' has no onLoad(); skipping initialization")
            
            loaded.append(LoadedPyMod(
                modId=manifest.id,
                name=manifest.name,
                version=manifest.version,
                module=module,
                entryPath=entryPath,
            ))
            logger.info(f"Loaded Python mod: '{manifest.id}@{manifest.version}'")
        except Exception as err:
            import traceback
            tb = traceback.format_exc()
            logger.exception(f"Failed to load Python mod {manifest.id}: {err}")
            failed.append({"id": manifest.id, "reason": str(err), "stack": tb})
    
    return loaded, failed, services
