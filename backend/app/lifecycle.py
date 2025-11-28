# backend/app/lifecycle.py
from __future__ import annotations
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from backend.app.context import PROCESS_REGISTRY
from backend.app.globals import (
    getActiveAppPack,
    getActiveRuntime,
    getAllowedModIds,
    getPermissions,
    getConfigService,
    getTracer
)
from backend.mods.discover import scanMods
from backend.mods.frontend_index import makeFrontendIndex
from backend.mods.loader import loadPythonMods
from backend.mods.runtime_state import ModRuntimeSnapshot, setModRuntimeSnapshot

logger = logging.getLogger(__name__)



@asynccontextmanager
async def life(app: FastAPI) -> AsyncIterator[None]:
    # --------------- Startup ---------------

    modsServices: dict[str, Any] = {}
    try:
        configService = getConfigService()
        
        runtimeInstance = getActiveRuntime()
        appPack = getActiveAppPack()
        allowedMods = getAllowedModIds()
        
        # Give mods a plain snapshot of merged global config
        configSnapshot: dict[str, Any] = configService.globalStore.snapshot()
        loaded, failed, services = await loadPythonMods(
            settings=configSnapshot,
            allowedModIds=allowedMods,
            appPack=appPack,
            saveRoot=getattr(runtimeInstance, "saveRoot", None),
        )
        
        frontendMods = scanMods(
            allowedIds=allowedMods,
            appPack=appPack,
            saveRoot=getattr(runtimeInstance, "saveRoot", None),
        )
        
        frontendIndex = makeFrontendIndex(
            frontendMods,
            base="/mods/load",
            mountId=None,
        )
        
        snapshot = ModRuntimeSnapshot(
            allowed=set(allowedMods),
            backendLoaded=[{"id": mod.modId, "name": mod.displayName, "version": mod.version} for mod in loaded],
            backendFailed=failed,
            frontendIndex=frontendIndex,
        )
        setModRuntimeSnapshot(snapshot)
        PROCESS_REGISTRY.register("mods.services", modsServices, overwrite=True)
        
        perms = getPermissions()
        perms.registerCapability(capability="http.client@1",  risk="high")
        perms.registerCapability(capability="chat@1",         risk="medium")
        perms.registerCapability(capability="gm.narration@1", risk="low")
        perms.registerCapability(capability="gm.world@1",     risk="low")
        perms.registerCapability(capability="chat.thread@1",  risk="low")
        perms.registerCapability(capability="chat.start@1",   risk="medium")
        perms.registerCapability(capability="main.menu@1",    risk="medium")
        perms.registerCapability(capability="trace.stream@1", risk="medium")
    except Exception as err:
        logger.exception("Python mod loading failed: %s", err)
    yield

    # --------------- Shutdown ---------------
    # Close any services that support aclose()
    for name, svc in list((PROCESS_REGISTRY.get("mods.services") or {}).items()):
        try:
            closer = getattr(svc, "aclose", None)
            res = closer() if callable(closer) else None
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            logger.exception("Error closing service '%s'", name)
    
    tracer = getTracer()
    tracer.endProcessSpan(status="ok")
