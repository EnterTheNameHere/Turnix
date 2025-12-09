# first-party/appPack/main-menu/mods/main-menu-service/main-menu-service.py
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import json5

from backend.app.bootstrap import (
    _canonicalAppPackId,
    _defaultAppInstanceId,
    _extractMods,
    _generateAppInstance,
)
from backend.app.context import PROCESS_REGISTRY
from backend.app.globals import (
    getConfigService,
    getKernel,
    getModServices,
    getContentRootsService,
)
from backend.app.persistence import loadAppInstance
from backend.content.packs import PackResolver, ResolvedPack
from backend.content.saves import SaveManager
from backend.core.logger import getModLogger
from backend.mods.loader import loadPythonMods
from backend.rpc.api import registerCapabilityInstance, unregisterCapability
from backend.rpc.broadcast import pushEvent
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
        if path == "generateAppInstance":
            payload = dict(args[0]) if args else {}
            async with self._lock:
                return await self._generateAppInstance(payload)
        if path == "loadSave":
            payload = dict(args[0]) if args else {}
            async with self._lock:
                return await self._loadSave(payload)
        raise ValueError(f"Unknown call path: {path}")
    
    def _listAppPacks(self) -> list[dict[str, Any]]:
        resolver = PackResolver()
        packs = resolver.listPacks(kinds={"appPack"})
        roots = getContentRootsService().contentRoots()
        
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
                "author": pack.authorName,
                "rootKind": _rootKind(pack.sourceRoot),
            })
        return out
    
    def _listSaves(self) -> list[dict[str, Any]]:
        savesRoot = getContentRootsService().getWriteDir("saves")
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
                        "appInstanceId": saveDir.name,
                        "label": manifest.get("label"),
                        "savedTs": manifest.get("savedTs"),
                    })
        except FileNotFoundError:
            return []
        except Exception:
            logger.exception("Listing saves failed")
        return entries
    
    async def _generateAppInstance(self, payload: dict[str, Any]) -> dict[str, Any]:
        resolver = PackResolver()
        # Prime resolver
        resolver.listPacks()
        appPack = self._requireAppPack(resolver, payload.get("appPackId"))
        
        canonicalId = _canonicalAppPackId(appPack)
        appInstanceId = str(payload.get("appInstanceId") or _defaultAppInstanceId(appPack))
        
        baseDir = getContentRootsService().getWriteDir("saves")
        appKey = SaveManager().appIdToKey(canonicalId)
        saveDir = _generateAppInstance(
            appPack=appPack,
            appKey=appKey,
            appInstanceId=appInstanceId,
            baseDir=baseDir,
        )
        
        appInstance = loadAppInstance(saveDir)
        await self._activateAppInstance(appInstance, appPack)
        
        return {
            "ok": True,
            "appPackId": canonicalId,
            "appInstanceId": appInstance.id,
            "saveDir": str(saveDir),
        }
    
    async def _loadSave(self, payload: dict[str, Any]) -> dict[str, Any]:
        resolver = PackResolver()
        # Prime resolver
        resolver.listPacks()
        
        appPack = self._requireAppPack(resolver, payload.get("appPackId"))
        appInstanceId = str(payload.get("appInstanceId") or "").strip()
        if not appInstanceId:
            raise ValueError("appInstanceId must be provided")
        
        binding = SaveManager().bind(appPackId=_canonicalAppPackId(appPack), instanceId=appInstanceId, create=False)
        manifestPath = binding.saveDir / "save.json5"
        if not manifestPath.exists():
            raise FileNotFoundError(f"Save manifest not found at {manifestPath}")
        
        appInstance = loadAppInstance(binding.saveDir)
        await self._activateAppInstance(appInstance, appPack)
        
        return {
            "ok": True,
            "appPackId": _canonicalAppPackId(appPack),
            "appInstanceId": appInstance.id,
        }
    
    def _requireAppPack(self, resolver: PackResolver, appPackId: Any) -> ResolvedPack:
        appPackId = str(appPackId or "").strip()
        if not appPackId:
            raise ValueError("appPackId must be provided")
        appPack = resolver.resolveAppPack(appPackId)
        if not appPack:
            raise ValueError(f"AppPack '{appPackId}' not found")
        return appPack
    
    async def _activateAppInstance(self, appInstance: Any, appPack: ResolvedPack) -> None:
        allowedMods = _extractMods(appPack)
        appInstance.setAllowedPacks(allowedMods)
        
        kernel = getKernel()
        kernel.switchAppInstance(appInstance)
        PROCESS_REGISTRY.register("appInstance.active.appPack", appPack, overwrite=True)
        
        frontendIndex, backendLoaded, backendFailed = await self._reloadMods(appInstance, appPack, allowedMods)
        await self._refreshViews(appPack, frontendIndex, backendLoaded, backendFailed)
    
    async def _reloadMods(
        self,
        appInstance: Any,
        appPack: ResolvedPack,
        allowedMods: set[str]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        cfgSnapshot = getConfigService().globalStore.snapshot()
        await self._closeServices(getModServices())
        
        loaded, failed, services = await loadPythonMods(
            settings=cfgSnapshot,
            allowedModIds=allowedMods,
            appPack=appPack,
            saveRoot=getattr(appInstance, "saveRoot", None),
        )
        
        print("\033[92m    >>>> _reloadMods >>>>\033[0m", "loaded", loaded, "failed", failed)
        
        appInstance.setAllowedPacks(set(allowedMods))
        appInstance.backendPacksLoaded = list(
            [
                {"id": mod.modId, "name": mod.name, "version": mod.version} for mod in loaded
            ]
        )
        appInstance.backendPacksFailed = list(failed)
        PROCESS_REGISTRY.register("mods.services", services, overwrite=True)
        
        return {}, appInstance.backendPacksLoaded, appInstance.backendPacksFailed
    
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
        
        # Ask clients to reload themselves so they reconnect with fresh appInstance state
        await pushEvent(
            "turnix.client",
            {
                "op": "reload",
                "reason": "appInstance_switched",
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
