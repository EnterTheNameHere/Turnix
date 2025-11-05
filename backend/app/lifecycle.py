# backend/app/lifecycle.py
from __future__ import annotations
import asyncio
import logging
from collections.abc import AsyncIterator # pyright: ignore[reportShadowedImports] - one of our requirement ships typings extra, but Python 3.12 already includes them
from contextlib import asynccontextmanager # pyright: ignore[reportShadowedImports]
from typing import Any

from fastapi import FastAPI

from backend.app import state
from backend.app.globals import getPermissions, getConfigService
from backend.mods.loader import loadPythonMods

logger = logging.getLogger(__name__)



@asynccontextmanager
async def life(app: FastAPI) -> AsyncIterator[None]:
    # --------------- Startup ---------------

    try:
        configService = getConfigService()
        
        # Give mods a plain snapshot of merged global config
        configSnapshot: dict[str, Any] = configService.globalStore.snapshot()
        loaded, failed, services = await loadPythonMods(settings=configSnapshot)
        
        # Publish into global state module
        state.SERVICES = services
        state.PYMODS_LOADED = [{"id": m.modId, "name": m.name, "version": m.version} for m in loaded]
        state.PYMODS_FAILED = failed

        perms = getPermissions()
        perms.registerCapability(capability="http.client@1", risk="high")
        perms.registerCapability(capability="chat@1",        risk="medium")
        perms.registerCapability(capability="gm.narration@1",risk="low")
        perms.registerCapability(capability="gm.world@1",    risk="low")
        perms.registerCapability(capability="chat.thread@1", risk="low")
        perms.registerCapability(capability="chat.start@1",  risk="medium")
    except Exception as err:
        logger.exception("Python mod loading failed: %s", err)
    yield

    # --------------- Shutdown ---------------
    # Close any services that support aclose()
    for name, svc in list(state.SERVICES.items()):
        try:
            closer = getattr(svc, "aclose", None)
            res = closer() if callable(closer) else None
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            logger.exception("Error closing service '%s'", name)
