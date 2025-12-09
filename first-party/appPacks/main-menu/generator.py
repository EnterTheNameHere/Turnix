# first-party/appPacks/main-menu/generator.py
"""
Generator for the Turnix main menu app pack.

This module is executed by the controller to ensure that the
main menu appInstance has an on-disk save to restore from.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

from backend.app.instance import AppInstance
from backend.app.persistence import save
from backend.memory.memory_layer import MemoryObject

logger = logging.getLogger(__name__)



APP_PACK_ID = "main-menu"
DEFAULT_APP_INSTANCE_ID = "turnix-main-menu"



def _coercePath(value: Any) -> Path | None:
    if value is None:
        return None
    try:
        return Path(value).expanduser().resolve()
    except Exception:
        return None



def generate(context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """
    Create a AppInstance for the main menu and persist its snapshot.
    """
    ctx = dict(context or {})
    appPackId = str(ctx.get("appPackId") or APP_PACK_ID)
    appInstanceId = str(ctx.get("appInstanceId") or DEFAULT_APP_INSTANCE_ID)
    saveDirOverride = _coercePath(ctx.get("saveDir"))
    label = str(ctx.get("label") or "Main Menu")
    
    # When a save directory is provided, use its parent as the base so the
    # appInstance writes exactly into the requested path.
    saveBase = saveDirOverride.parent.parent if saveDirOverride else None
    
    appInstance = AppInstance(
        appPackId=appPackId,
        appInstanceId=appInstanceId,
        saveBaseDirectory=saveBase,
        createMainSession=True,
    )
    
    # Provide a small amount of runtime-local state so the frontend can greet
    # the user even before other mods start populating memory.
    appInstance.runtimeMemory.set(
        "menu.banner",
        MemoryObject(
            id="menu.banner",
            payload={
                "title": "Turnix",
                "subtitle": "Welcome to the experimental launcher"
            },
            originLayer="runtime",
        ),
    )
    
    manifestPath, _hash = save(appInstance, appInstance.saveRoot, label=label)
    logger.info("Generated main menu appInstance save as %s", manifestPath.parent)

    return {
        "saveDir": str(appInstance.saveRoot),
        "appInstanceId": appInstance.id,
        "appPackId": appInstance.appPackId,
    }
