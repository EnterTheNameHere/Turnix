# backend/mods/loader.py
from __future__ import annotations
import asyncio
import importlib.util
import logging
from typing import Any # pyright: ignore[reportShadowedImports]
from dataclasses import dataclass
from pathlib import Path

from backend.app.state import PERMS
from backend.core.permissions import PermissionManager, GrantPermission, parseCapabilityRange
from backend.mods.constants import PY_RUNTIMES
from backend.mods.discover import scanMods
from backend.mods.manifest import RuntimeSpec, ModManifest

logger = logging.getLogger(__name__)



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
        logger.info("Registered backend service '%s' via Python mod", name)



async def loadPythonMods(*, settings: dict[str, Any]) -> tuple[list[LoadedPyMod], list[dict[str, Any]], dict[str, Any]]:
    """
    Returns (loaded, failed, services) where:
      - loaded: list of LoadedPyMod
      - failed: list of {id, reason, stack?}
      - services: mapping registered by mods via ctx.registerService(name, instance)
    """
    discovered = scanMods()

    # TODO: replace with proper permission prompting/flow
    autoGrantPermissionsForMods(PERMS, discovered)

    enabled: list[tuple[ModManifest, Path, Path, RuntimeSpec]] = []

    for _modId, (_root, moddir, manifest, _manFileName) in discovered.items():
        rt = next((manifest.runtimes[key] for key in PY_RUNTIMES if key in manifest.runtimes), None)
        if not rt or not rt.enabled:
            continue

        entryPath = moddir / rt.entry
        enabled.append((manifest, moddir, entryPath, rt))
    
    services: dict[str, Any] = {}
    loaded: list[LoadedPyMod] = []
    failed: list[dict[str, Any]] = []

    def _sortKey(entry):
        manifest, _moddir, _entryPath, runtime = entry
        return (runtime.order, manifest.id, manifest.version)

    for manifest, moddir, entryPath, rt in sorted(enabled, key=_sortKey):
        try:
            if not entryPath.exists():
                raise FileNotFoundError(f"'{manifest.id}' - entry file not found: '{entryPath}'")

            module = _quickImport(entryPath.resolve())
            onLoad = getattr(module, "onLoad", None)

            ctx = PyModContext(services=services, settings=settings)
            if asyncio.iscoroutinefunction(onLoad):
                await onLoad(ctx) # async
            elif callable(onLoad):
                onLoad(ctx)       # sync
            else:
                logger.info("Python mod '%s' has no onLoad(); skipping initialization", manifest.id)
            
            loaded.append(LoadedPyMod(
                modId=manifest.id,
                name=manifest.name,
                version=manifest.version,
                module=module,
                entryPath=entryPath,
            ))
            logger.info("Loaded Python mod: '%s@%s'", manifest.id, manifest.version)
        except Exception as err:
            import traceback
            tb = traceback.format_exc()
            logger.exception("Failed to load Python mod '%s': %s", manifest.id, err)
            failed.append({
                "id": manifest.id,
                "runtime": "python",
                "entry": str(entryPath),
                "reason": str(err),
                "stack": tb,
            })
    
    return loaded, failed, services



def autoGrantPermissionsForMods(perms: PermissionManager, discovered: dict[str, tuple[Path, Path, ModManifest, str]]) -> None:
    """
    Iterate manifests/runtimes and grant requested permissions programatically for each modId.
    """
    grantedCount = 0
    for _modId, (_root, _moddir, manifest, _manFileName) in discovered.items():
        principal = manifest.id
        # Each runtime can request permissions
        for rt in (manifest.runtimes or {}).values():
            for permStr in (rt.permissions or []):
                try:
                    family, rangeSpec = parseCapabilityRange(permStr) # May raise on invalid range
                except Exception as err:
                    logger.warning("Slipping invalid permission '%s' in mod '%s': %s", permStr, principal, err)
                    continue
                if not family:
                    continue
                perms.putGrant(GrantPermission(
                    principal=principal,
                    family=family,
                    rangeSpec=rangeSpec,
                    decision="allow",
                    scope=None,
                    expiresAtMs=None,
                ))
                logger.info("Auto-granted permission '%s' to '%s' (range '%s')", family, principal, str(rangeSpec))
                grantedCount += 1
    if grantedCount:
        logger.info("Auto-granted %d permission(s) from mod manifests.", grantedCount)



def _quickImport(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
