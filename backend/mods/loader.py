# backend/mods/loader.py
from __future__ import annotations

import asyncio
import importlib.util
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.globals import getPermissions, getTracer
from backend.core.permissions import PermissionManager, GrantPermission, parseCapabilityRange
from backend.content.packs import ResolvedPack
from backend.mods.constants import PY_RUNTIMES
from backend.mods.discover import scanMods, ModMap
from backend.mods.roots_registry import getRoots as getRegisteredRoots
from backend.mods.manifest import RuntimeSpec, ModManifest

logger = logging.getLogger(__name__)



@dataclass(frozen=True)
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



async def loadPythonMods(
    *,
    settings: dict[str, Any],
    allowedModIds: Iterable[str] | None = None,
    appPack: ResolvedPack | None = None,
    saveRoot: Path | None = None,
    extraRoots: Iterable[Path] | None = None,
) -> tuple[list[LoadedPyMod], list[dict[str, Any]], dict[str, Any]]:
    """
    Returns (loaded, failed, services) where:
      - loaded: list of LoadedPyMod
      - failed: list of {id, reason, stack?}
      - services: mapping registered by mods via ctx.registerService(name, instance)
    
    extraRoots:
      Optional iterable of additional pack roots for this load, typically used
      by view context to include viewPack-local mods.
    """
    tracer = getTracer()
    span = None
    
    try:
        span = tracer.startSpan(
            "mods.load.python",
            attrs={
                "settingsKeys": sorted(settings.keys()),
            },
            tags=["mods", "python"],
        )
        tracer.traceEvent(
            "mods.load.python.start",
            level="info",
            tags=["mods", "python"],
            span=span,
        )
    except Exception:
        span = None
    
    try:
        discovered: ModMap = scanMods(
            allowedIds=allowedModIds,
            appPack=appPack,
            saveRoot=saveRoot,
            extraRoots=extraRoots
        )

        if span is not None:
            try:
                tracer.traceEvent(
                    "mods.load.python.discovered",
                    level="debug",
                    tags=["mods", "python"],
                    span=span,
                    attrs={"modCount": len(discovered)},
                )
            except Exception:
                pass
        
        # TODO: replace with proper permission prompting/flow
        autoGrantPermissionsForMods(getPermissions(), discovered)

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

        for manifest, _moddir, entryPath, rt in sorted(enabled, key=_sortKey):
            
            # Enrich ambient trace context so all records get modId/modRuntime
            try:
                tracer.updateTraceContext({
                    "modId": manifest.id,
                    "modRuntime": "python",
                })
            except Exception:
                # Context is best-effort. Ignore failures.
                pass
            
            if span is not None:
                try:
                    tracer.traceEvent(
                        "mods.load.python.modStart",
                        level="debug",
                        tags=["mods", "python"],
                        span=span,
                        attrs={
                            "modId": manifest.id,
                            "version": manifest.version,
                            "entryPath": str(entryPath),
                            "order": rt.order,
                        },
                    )
                except Exception:
                    pass
            
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
                
                if span is not None:
                    try:
                        tracer.traceEvent(
                            "mods.load.python.modLoaded",
                            level="debug",
                            tags=["mods", "python"],
                            span=span,
                            attrs={
                                "modId": manifest.id,
                                "version": manifest.version,
                            },
                        )
                    except Exception:
                        pass
                
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
                
                if span is not None:
                    try:
                        tracer.traceEvent(
                            "mods.load.python.modFailed",
                            level="error",
                            tags=["mods", "python", "error"],
                            span=span,
                            attrs={
                                "modId": manifest.id,
                                "version": manifest.version,
                                "errorType": type(err).__name__,
                                "errorMessage": str(err),
                            },
                        )
                    except Exception:
                        pass
        
        if span is not None:
            try:
                tracer.traceEvent(
                    "mods.load.python.done",
                    level="info",
                    tags=["mods", "python"],
                    span=span,
                    attrs={
                        "loadedCount": len(loaded),
                        "failedCount": len(failed),
                    },
                )
                tracer.endSpan(
                    span,
                    status="ok",
                    tags=["mods", "python"],
                )
            except Exception:
                pass
                
        return loaded, failed, services
    
    except Exception as err:
        if span is not None:
            try:
                tracer.traceEvent(
                    "mods.load.python.error",
                    level="error",
                    tags=["mods", "python", "error"],
                    span=span,
                    attrs={
                        "errorType": type(err).__name__,
                        "errorMessage": str(err),
                    },
                )
                tracer.endSpan(
                    span,
                    status="error",
                    tags=["mods", "python", "error"],
                    errorType=type(err).__name__,
                    errorMessage=str(err),
                )
            except Exception:
                pass
        
        raise



async def loadPythonModsForMount(
    viewKind: str,
    *,
    settings: dict[str, Any],
    allowedModIds: Iterable[str] | None = None,
    appPack: ResolvedPack | None = None,
    saveRoot: Path | None = None,
) -> tuple[list[LoadedPyMod], list[dict[str, Any]], dict[str, Any]]:
    """
    Convenience wrapper to load Python mods for a specific view/mount.
    
    Resolution rules mirror scanModsForMount():
      - Mount-specific roots (registered for this viewKind)
      - Optional appPack.rootDir
      - Optional saveRoot
      - Global content roots
    """
    extraRoots = getRegisteredRoots(viewKind) or ()
    return await loadPythonMods(
        settings=settings,
        allowedModIds=allowedModIds,
        appPack=appPack,
        saveRoot=saveRoot,
        extraRoots=extraRoots,
    )



def autoGrantPermissionsForMods(perms: PermissionManager, discovered: ModMap) -> None:
    """
    Iterate manifests/runtimes and grant requested permissions programmatically for each modId.
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



def _quickImport(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
