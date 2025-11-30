# first-party/appPack/main-menu/mods/main-menu-service/main-menu-service.py
from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Any

import json5

from backend.app.context import PROCESS_REGISTRY
from backend.app.globals import (
    getConfigService,
    getKernel,
    getModServices,
    getRootsService,
)
from backend.content.packs import PackResolver, ResolvedPack
from backend.content.runtime_bootstrap import (
    _canonicalAppPackId,
    _defaultInstanceId,
    _extractMods,
    _generateRuntime,
)
from backend.content.saves import SaveManager
from backend.core.logger import getModLogger
from backend.mods.loader import loadPythonMods
from backend.rpc.api import registerCapabilityInstance, unregisterCapability
from backend.rpc.broadcast import pushEvent
from backend.runtimes.persistence import loadRuntime
from backend.views.registry import viewRegistry

logger = getModLogger("main-menu-service")

_CAP_NAME = "main.menu@1"



def onLoad(_ctx) -> None:
    try:
        unregisterCapability(_CAP_NAME)
    except Exception:
        logger.debug("Failed to unregister previous capability binding", exc_info=True)
    
    registerCapabilityInstance(_CAP_NAME, _MainMenuCapability())
    logger.info("Registered capability '%s'", _CAP_NAME)



class _MainMenuCapability:
    def __init__(self) -> None:
        self._lock: asyncio.Lock = asyncio.Lock()
    
    async def call(self, path: str, args: list[Any], _ctx) -> Any:
        if path == "listAppPacks":
            return {"appPacks": self._listAppPacks()}
        if path == "listSaves":
            return {"saves": self._listSaves()}
        if path == "generateRuntime":
            payload = dict(args[0]) if args else {}
            async with self._lock:
                return await self._generateRuntime(payload)
        if path == "loadSave":
            payload = dict(args[0]) if args else {}
            async with self._lock:
                return await self._loadSave(payload)
        raise ValueError(f"Unknown call path: {path}")
    
    def _listAppPacks(self) -> list[dict[str, Any]]:
        resolver = PackResolver()
        packs = resolver.listPacks(kinds={"appPack"})
        roots = getRootsService().contentRoots()
        
        def _rootKind(path: Path | None) -> str | None:
            if not path:
                return None
            for root in roots:
                try:
                    if path.is_relative_to(root):
                        return root.name
                except ValueError:
                    continue
            return None
        
        out: list[dict[str, Any]] = []
        for pack in packs:
            canonical = _canonicalAppPackId(pack)
            out.append({
                "id": canonical,
                "name": pack.name,
                "version": pack.version,
                "author": pack.author,
                "rootKind": _rootKind(pack.sourceRoot),
            })
        return out
    
    def _listSaves(self) -> list[dict[str, Any]]:
        savesRoot = getRootsService().getWriteDir("saves")
        entries: list[dict[str, Any]] = []
    
        def _readManifest(saveDir: Path) -> dict[str, Any] | None:
            manifestPath = saveDir / "save.json5"
            if not manifestPath.exists():
                return None
            try:
                rawJson = json5.loads(manifestPath.read_text(encoding="utf-8"))
                return rawJson if isinstance(rawJson, dict) else None
            except Exception:
                logger.debug("Failed to read manifest at %s", manifestPath, exc_info=True)
                return None
        
        try:
            for appDir in sorted(path for path in savesRoot.iterdir() if path.is_dir()):
                for saveDir in sorted(path2 for path2 in appDir.iterdir() if path2.is_dir()):
                    manifest = _readManifest(saveDir) or {}
                    entries.append({
                        "appPackId": appDir.name,
                        "runtimeInstanceId": saveDir.name,
                        "label": manifest.get("label"),
                        "savedTs": manifest.get("savedTs"),
                    })
        except FileNotFoundError:
            return []
        except Exception:
            logger.exception("Listing saves failed")
        return entries
    
    async def _generateRuntime(self, payload: dict[str, Any]) -> dict[str, Any]:
        resolver = PackResolver()
        # Prime resolver
        resolver.listPacks()
        appPack = self._requireAppPack(resolver, payload.get("appPackId"))
        
        canonicalId = _canonicalAppPackId(appPack)
        runtimeInstanceId = str(payload.get("runtimeInstanceId") or _defaultInstanceId(appPack))
        
        baseDir = getRootsService().getWriteDir("saves")
        appKey = SaveManager().appIdToKey(canonicalId)
        saveDir = _generateRuntime(
            appPack=appPack,
            appKey=appKey,
            runtimeInstanceId=runtimeInstanceId,
            baseDir=baseDir,
        )
        
        runtimeInstance = loadRuntime(saveDir)
        await self._activateRuntime(runtimeInstance, appPack)
        
        return {
            "ok": True,
            "appPackId": canonicalId,
            "runtimeInstanceId": runtimeInstance.id,
            "saveDir": str(saveDir),
        }
    
    async def _loadSave(self, payload: dict[str, Any]) -> dict[str, Any]:
        resolver = PackResolver()
        # Prime resolver
        resolver.listPacks()
        
        appPack = self._requireAppPack(resolver, payload.get("appPackId"))
        runtimeInstanceId = str(payload.get("runtimeInstanceId") or "").strip()
        if not runtimeInstanceId:
            raise ValueError("runtimeInstanceId must be provided")
        
        binding = SaveManager().bind(appPackId=_canonicalAppPackId(appPack), instanceId=runtimeInstanceId, create=False)
        manifestPath = binding.saveDir / "save.json5"
        if not manifestPath.exists():
            raise FileNotFoundError(f"Save manifest not found at {manifestPath}")
        
        runtimeInstance = loadRuntime(binding.saveDir)
        await self._activateRuntime(runtimeInstance, appPack)
        
        return {
            "ok": True,
            "appPackId": _canonicalAppPackId(appPack),
            "runtimeInstanceId": runtimeInstance.id,
        }
    
    def _requireAppPack(self, resolver: PackResolver, appPackId: Any) -> ResolvedPack:
        appPackId = str(appPackId or "").strip()
        if not appPackId:
            raise ValueError("appPackId must be provided")
        appPack = resolver.resolveAppPack(appPackId)
        if not appPack:
            raise ValueError(f"AppPack '{appPackId}' not found")
        return appPack
    
    async def _activateRuntime(self, runtimeInstance: Any, appPack: ResolvedPack) -> None:
        allowedMods = _extractMods(appPack)
        runtimeInstance.setAllowedPacks(allowedMods)
        
        kernel = getKernel()
        kernel.switchRuntime(runtimeInstance)
        PROCESS_REGISTRY.register("runtime.active.appPack", appPack, overwrite=True)
        
        frontendIndex, backendLoaded, backendFailed = await self._reloadMods(runtimeInstance, appPack, allowedMods)
        await self._refreshViews(appPack, frontendIndex, backendLoaded, backendFailed)
    
    async def _reloadMods(
        self,
        runtimeInstance: Any,
        appPack: ResolvedPack,
        allowedMods: set[str]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        cfgSnapshot = getConfigService().globalStore.snapshot()
        await self._closeServices(getModServices())
        
        loaded, failed, services = await loadPythonMods(
            settings=cfgSnapshot,
            allowedModIds=allowedMods,
            appPack=appPack,
            saveRoot=getattr(runtimeInstance, "saveRoot", None),
        )
        
        print("\033[92m    >>>> _reloadMods >>>>\033[0m", "loaded", loaded, "failed", failed)
        
        runtimeInstance.setAllowedPacks(set(allowedMods))
        runtimeInstance.backendPacksLoaded = list([{"id": mod.modId, "name": mod.displayName, "version": mod.version} for mod in loaded])
        runtimeInstance.backendPacksFailed = list(failed)
        PROCESS_REGISTRY.register("mods.services", services, overwrite=True)
        
        return {}, runtimeInstance.backendPacksLoaded, runtimeInstance.backendPacksFailed
    
    async def _refreshViews(
        self,
        appPack: ResolvedPack,
        frontendIndex: dict[str, Any],
        backendLoaded: list[dict[str, Any]],
        backendFailed: list[dict[str, Any]],
    ) -> None:
        statePatch = {
            "mods": {
                "frontend": frontendIndex,
                "backend": {
                    "loaded": backendLoaded,
                    "failed": backendFailed,
                },
            }
        }
        canonicalId = _canonicalAppPackId(appPack)
        for view in list(viewRegistry.viewsById.values()):
            try:
                view.setAppPackId(canonicalId)
                view.patchState(statePatch)
                view.refreshFrontendIndex()
            except Exception:
                logger.debug("Failed to refresh view '%s'", getattr(view, "id", "?"), exc_info=True)
        
        # Ask clients to reload themselves so they reconnect with fresh runtime state
        await pushEvent(
            "turnix.client",
            {
                "op": "reload",
                "reason": "runtime_instance_switched",
            },
        )
    
    async def _closeServices(self, services: dict[str, Any]) -> None:
        for name, svc in list(services.items()):
            try:
                closer = getattr(svc, "aclose", None)
                res = closer() if callable(closer) else None
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                logger.debug("Error closing service '%s'", name, exc_info=True)
