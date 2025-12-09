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
    getActiveAppInstance,
    getPermissions,
    getConfigService,
    getTracer
)
from backend.mods.loader import loadPythonMods

logger = logging.getLogger(__name__)



@asynccontextmanager
async def life(app: FastAPI) -> AsyncIterator[None]:
    # --------------- Startup ---------------

    modsServices: dict[str, Any] = {}
    try:
        configService = getConfigService()
        
        appInstance = getActiveAppInstance()
        appPack = getActiveAppPack()
        allowedMods = appInstance.getAllowedPacks()
        
        # Give mods a plain snapshot of merged global config
        configSnapshot: dict[str, Any] = configService.globalStore.snapshot()
        loaded, failed, services = await loadPythonMods(
            settings=configSnapshot,
            allowedModIds=allowedMods,
            appPack=appPack,
            saveRoot=getattr(appInstance, "saveRoot", None),
        )
        
        
        appInstance.setAllowedPacks(set(allowedMods))
        appInstance.backendPacksLoaded = list(
            [
                {"id": mod.modId, "name": mod.name, "version": mod.version} for mod in loaded
            ]
        )
        appInstance.backendPacksFailed = list(failed)
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
