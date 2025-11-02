# backend/runtimes/base.py
from __future__ import annotations
import time
from pathlib import Path
from typing import Any

from backend.core.ids import uuid_12
from backend.memory.memory_layer import (
    DictMemoryLayer,
    ReadOnlyMemoryLayer,
    MemoryLayer,
)
from backend.sessions.session import Session, SessionKind, SessionVisibility

__all__ = ["BaseRuntime"]



class BaseRuntime:
    """
    Generic runtime:
    - has its own runtime/static memory
    - can create sessions and inject these layers into them
    - defines a base dir where its sessions can persist
    """
    def __init__(
        self,
        *,
        runtimeId: str | None = None,
        kernelMemoryLayers: list[MemoryLayer] | None = None,
        saveRoot: Path | str | None = None,
    ) -> None:
        self.id = runtimeId or uuid_12("runtime_")
        self.createdTs: float = time.time()
        self.version: int = 0

        # Runtime-local (e.g. game state, menu state)
        self.runtimeMemory: MemoryLayer = DictMemoryLayer("runtime")
        self.staticMemory: MemoryLayer = ReadOnlyMemoryLayer("static", {})

        # Kernel-level bottom layers (if kernel passed them)
        self.kernelBottom: list[MemoryLayer] = list(kernelMemoryLayers) if kernelMemoryLayers else []

        # Sessions owned by this runtime
        self.sessionsById: dict[str, Session] = {}

        # Where this runtime wants to store its sessions
        # default: saves/runtimes/<runtimeId>/
        if saveRoot is None:
            self.saveRoot: Path = Path("saves") / "runtimes" / self.id
        else:
            self.saveRoot = Path(saveRoot)
        
        if self.saveRoot is not None:
            self.saveRoot.mkdir(parents=True, exist_ok=True)

    def makeSession(
        self,
        *,
        kind: SessionKind,
        sessionId: str | None = None,
        ownerViewId: str | None = None,
        visibility: SessionVisibility = "public",
    ) -> Session:
        # Order: higher to lower; kernel is last = lowest priority (gets accessed as last)
        bottom: list[MemoryLayer] = [
            self.runtimeMemory,
            self.staticMemory,
            *self.kernelBottom,
        ]

        sess = Session(
            kind=kind,
            sessionId=sessionId,
            ownerViewId=ownerViewId,
            visibility=visibility,
            sharedBottomLayers=bottom,
            savePath=self.saveRoot,
        )

        self.sessionsById[sess.id] = sess
        self.version += 1
        return sess
    
    def getSession(self, sessionId: str) -> Session | None:
        return self.sessionsById.get(sessionId)
    
    def destroySession(self, sessionId: str) -> dict[str, Any]:
        sess = self.sessionsById.get(sessionId)
        if not sess:
            raise KeyError(f"session '{sessionId}' does not exist")
        sess.destroy()
        del self.sessionsById[sessionId]
        self.version += 1
        return {"ok": True, "version": self.version}

    def listSessions(self, *, kind: str | None = None) -> list[str]:
        """Return session ids, optionally filtered by kind."""
        if kind is None:
            return sorted(self.sessionsById.keys())
        return sorted([sid for sid, sess in self.sessionsById.items() if sess.kind == kind])

    def snapshot(self) -> dict[str, object]:
        return {
            "runtimeId": self.id,
            "version": self.version,
            "createdTs": self.createdTs,
            "sessions": {sid: sess.snapshot() for sid, sess in self.sessionsById.items()},
        }

    def destroy(self, *, keepMain: bool = False) -> None:
        for sessId, sess in list(self.sessionsById.items()):
            if not sess:
                continue
            if keepMain and sess.kind == "main":
                continue
            try:
                sess.destroy()
            finally:
                self.sessionsById.pop(sessId, None)
        self.version += 1
