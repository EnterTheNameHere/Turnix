# backend/content/saves.py
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast, Literal

import json5

from backend.app.globals import getContentRootsService

__all__ = [
    "SaveDescriptor",
    "SaveManager",
]



_ID_SAFE = re.compile(r"^[A-Za-z0-9_.@-]+$")



@dataclass(frozen=True)
class SaveDescriptor:
    appPackId: str
    instanceId: str
    saveDir: Path



class SaveManager:
    """
    Knows where to put userdata/saves, creates directories, and lists existing saves.
    Uses ContentRootsService precedence to choose the first writable saves/ root that exists (or can be created).
    """
    def _firstWritable(self, kind: Literal["userdata", "saves"]) -> Path:
        roots = getContentRootsService()
        try:
            base = roots.getWriteDir(kind)
        except Exception as err:
            raise RuntimeError(f"Cannot obtain writable root for kind='{kind}': {err}") from err
        return base
    
    def appIdToKey(self, appPackId: str) -> str:
        """
        Converts the user-supplied id into a filesystem key (keeps current charset).
        """
        appPackId = (appPackId or "").strip()
        if not appPackId or not _ID_SAFE.fullmatch(appPackId):
            raise ValueError("appPackId contains invalid characters")
        return appPackId
    
    def _validateInstanceId(self, instanceId: str) -> str:
        instanceId = str(instanceId or "").strip()
        if not instanceId or not _ID_SAFE.fullmatch(instanceId):
            raise ValueError("instanceId contains invalid characters")
        return instanceId
    
    def getSaveDir(self, appPackId: str, instanceId: str) -> Path:
        base = self._firstWritable("saves")
        appKey = self.appIdToKey(appPackId)
        instanceId = self._validateInstanceId(instanceId)
        path = (base / appKey / instanceId).resolve()
        return path
    
    def bind(self, appPackId: str, instanceId: str, *, create: bool = False) -> SaveDescriptor:
        saveDir = self.getSaveDir(appPackId, instanceId)
        if create:
            try:
                saveDir.mkdir(parents=True, exist_ok=True)
            except Exception as err:
                raise IOError(f"Failed to create save directory '{saveDir}': {err}") from err
        return SaveDescriptor(
            appPackId=appPackId,
            instanceId=instanceId,
            saveDir=saveDir,
        )
    
    def listSaves(self, appPackId: str) -> list[SaveDescriptor]:
        base = self._firstWritable("saves")
        appKey = self.appIdToKey(appPackId)
        root = (base / appKey).resolve()
        if not root.exists() or not root.is_dir():
            return []
        out: list[SaveDescriptor] = []
        try:
            for child in root.iterdir():
                if child.is_dir():
                    out.append(SaveDescriptor(
                        appPackId=appPackId,
                        instanceId=child.name,
                        saveDir=child.resolve(),
                    ))
        except Exception:
            # If listing fails, return what we collected so far.
            pass
        out.sort(key=lambda save: save.instanceId)
        return out
    
    def readSaveMeta(self, saveDir: Path) -> dict[str, Any] | None:
        meta = saveDir / "meta.json5"
        if not meta.exists():
            return None
        try:
            return cast(dict[str, Any], json5.loads(meta.read_text(encoding="utf-8")))
        except Exception:
            return None
    
    def writeSaveMeta(self, saveDir: Path, data: dict[str, Any]) -> None:
        meta = saveDir / "meta.json5"
        try:
            meta.parent.mkdir(parents=True, exist_ok=True)
            meta.write_text(json5.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as err:
            raise IOError(f"Failed to write save meta: {err}") from err
