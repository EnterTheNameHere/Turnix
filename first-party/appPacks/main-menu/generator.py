# first-party/appPacks/main-menu/generator.py
"""
Generator for the Turnix main menu app pack.

This module is executed by the runtime controller to ensure that the
main menu runtime has an on-disk save to restore from.
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Any, Mapping

from backend.memory.memory_layer import MemoryObject
from backend.runtimes.instance import RuntimeInstance
from backend.runtimes.persistence import save

logger = logging.getLogger(__name__)



APP_PACK_ID = "Turnix@main-menu"
DEFAULT_RUNTIME_INSTANCE_ID = "turnix-main-menu"



def _coercePath(value: Any) -> Path | None:
    if value is None:
        return None
    try:
        return Path(value).expanduser().resolve()
    except Exception:
        return None



def generate(context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """
    Create a RuntimeInstance for the main menu and persist its snapshot.
    """
    ctx = dict(context or {})
    appPackId = str(ctx.get("appPackId") or APP_PACK_ID)
    runtimeInstanceId = str(ctx.get("runtimeInstanceId") or DEFAULT_RUNTIME_INSTANCE_ID)
    saveDirOverride = _coercePath(ctx.get("saveDir"))
    label = str(ctx.get("label") or "Main Menu")
    
    # When a save directory is provided, use its parent as the base so the
    # runtime writes exactly into the requested path.
    saveBase = saveDirOverride.parent.parent if saveDirOverride else None
    
    runtime = RuntimeInstance(
        appPackId=appPackId,
        runtimeInstanceId=runtimeInstanceId,
        saveBaseDirectory=saveBase,
        createMainSession=True,
    )
    
    # Provide a small amount of runtime-local state so the frontend can greet
    # the user even before other mods start populating memory.
    runtime.runtimeMemory.set(
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
    
    manifestPath, _hash = save(runtime, runtime.saveRoot, label=label)
    logger.info("Generated main menu runtime save as %s", manifestPath.parent)

    return {
        "saveDir": runtime.saveRoot,
        "runtimeInstanceId": runtime.id,
        "appPackId": runtime.appPackId,
    }
