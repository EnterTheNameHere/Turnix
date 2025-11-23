# first-party/appPacks/ai-chat/generator.py
from __future__ import annotations
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from backend.memory.memory_layer import MemoryObject
from backend.runtimes.instance import RuntimeInstance
from backend.runtimes.persistence import save

APP_PACK_ID = "Turnix@ai-chat"
APP_PACK_DISPLAY_NAME = "AI Chat"
DEFAULT_RUNTIME_INSTANCE_ID = "ai-chat-runtime"

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
    runtimeInstanceId = str(ctx.get("runtimeInstanceId") or DEFAULT_RUNTIME_INSTANCE_ID)
    label = str(ctx.get("label") or APP_PACK_DISPLAY_NAME)
    saveDirOverride = _coercePath(ctx.get("saveDir"))
    saveBase = saveDirOverride.parent if saveDirOverride else None
    
    runtimeInstance = RuntimeInstance(
        appPackId=appPackId,
        runtimeInstanceId=runtimeInstanceId,
        saveBaseDirectory=saveBase,
        createMainSession=True,
    )
    
    runtimeInstance.runtimeMemory.set(
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
    
    manifestPath, _hash = save(runtimeInstance, runtimeInstance.saveRoot, label=label)
    logger.info("Generated '%s' runtime save as %s", appPackId, manifestPath.parent)
    
    return {
        "saveDir": runtimeInstance.saveRoot,
        "runtimeInstanceId": runtimeInstanceId,
        "appPackId": appPackId,
    }
