# first-party/appPacks/ai-chat/generator.py
from __future__ import annotations
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from backend.memory.memory_layer import MemoryObject
from backend.app.instance import AppInstance
from backend.runtimes.persistence import save

APP_PACK_ID = "ai-chat"
APP_PACK_DISPLAY_NAME = "AI Chat"
DEFAULT_APP_INSTANCE_ID = "ai-chat-appInstance"

logger = logging.getLogger(__name__)



def _coercePath(value: Any) -> Path | None:
    if value is None:
        return None
    try:
        return Path(value).expanduser().resolve()
    except Exception:
        return None



def generate(context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    ctx = dict(context or {})
    appPackId = str(ctx.get("appPackId") or APP_PACK_ID)
    appInstanceId = str(ctx.get("appInstanceId") or DEFAULT_APP_INSTANCE_ID)
    label = str(ctx.get("label") or APP_PACK_DISPLAY_NAME)
    # When a save directory is provided, use its parent as the base of the saves root
    # so the appInstance writes exactly into the requested path (.../saves/appPackId/appInstanceId).
    saveDirOverride = _coercePath(ctx.get("saveDir"))
    saveBase = saveDirOverride.parent.parent if saveDirOverride else None
    
    appInstance = AppInstance(
        appPackId=appPackId,
        appInstanceId=appInstanceId,
        saveBaseDirectory=saveBase,
        createMainSession=True,
    )
    
    appInstance.runtimeMemory.set(
        "chat.banner",
        MemoryObject(
            id="chat.banner",
            payload={
                "title": "AI Chat",
                "subtitle": "Start a new conversation",
            },
            originLayer="runtime",
        ),
    )
    
    manifestPath, _hash = save(appInstance, appInstance.saveRoot, label=label)
    logger.info("Generated '%s' appInstance save as %s", appPackId, manifestPath.parent)
    
    return {
        "saveDir": appInstance.saveRoot,
        "appInstanceId": appInstance.id,
        "appPackId": appInstance.appPackId,
    }
