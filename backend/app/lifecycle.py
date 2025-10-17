# backend/app/lifecycle.py
from __future__ import annotations
import asyncio
import logging
from collections.abc import AsyncIterator # pyright: ignore[reportShadowedImports] - one of our requirement ships typings extra, but Python 3.12 already includes them
from contextlib import asynccontextmanager # pyright: ignore[reportShadowedImports]
from typing import Any

from fastapi import FastAPI

from backend.app.settings import loadSettings
from backend.app import state
from backend.mods.loader import loadPythonMods

logger = logging.getLogger(__name__)



@asynccontextmanager
async def life(app: FastAPI) -> AsyncIterator[None]:
    # --------------- Startup ---------------
    raw_settings = loadSettings()
    settings: dict[str, Any] = raw_settings if isinstance(raw_settings, dict) else {}

    try:
        loaded, failed, services = await loadPythonMods(settings=settings)
        
        # Publish into global state module
        state.SERVICES = services
        state.PYMODS_LOADED = [{"id": m.modId, "name": m.name, "version": m.version} for m in loaded]
        state.PYMODS_FAILED = failed

        state.PERMS.registerCapability(capability="http.client@1", risk="high")
        state.PERMS.registerCapability(capability="chat@1",        risk="medium")
        state.PERMS.registerCapability(capability="gm.narration@1",risk="low")
        state.PERMS.registerCapability(capability="gm.world@1",    risk="low")
        state.PERMS.registerCapability(capability="chat.thread@1", risk="low")
        state.PERMS.registerCapability(capability="chat.start@1",  risk="medium")

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
