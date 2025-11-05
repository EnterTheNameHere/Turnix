# backend/runtimes/base.py
from __future__ import annotations
import logging
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

__all__ = ["RuntimeInstance"]

logger = logging.getLogger(__name__)



class RuntimeInstance:
    """
    Base class for the runtime, representing an active, in-memory instance.
    Manages active sessions, runtime state, and mod interactions.
    """
    def __init__(
        self,
        *,
        appPackId: str, # id of the appPack which is this instance based on
        runtimeInstanceId: str | None = None, # id of the runtime instance
        saveBaseDirectory: Path | str | None = None, # overrides save directory; uses default saves@ otherwise
        kernelMemoryLayers: list[MemoryLayer] | None = None,
        createMainSession: bool = True,
    ) -> None:
        self.appPackId = appPackId.strip()
        if not self.appPackId:
            raise ValueError(f"appPackId must be a non-empty string")
        logger.debug("runtimeInstanceId: '%s', appPackId: '%s', saveBaseDirectory: '%s', kernelMemoryLayers: '%d', createMainSession: '%s'",
            runtimeInstanceId,
            appPackId,
            saveBaseDirectory,
            len(kernelMemoryLayers) if kernelMemoryLayers else 0,
            "True" if createMainSession is True else "False",
        )
        
        self.id = runtimeInstanceId or uuid_12("runtimeInstance_")
        
        baseSaveDir = Path(saveBaseDirectory) if saveBaseDirectory is not None else Path("saves")
        logger.debug("baseSaveDir '%s'", baseSaveDir)
        self.saveRoot: Path = baseSaveDir / self.appPackId / self.id
        logger.debug("RuntimeInstance '%s' created at '%s'", self.id, self.saveRoot)
        self.saveRoot.mkdir(parents=True, exist_ok=True)
        
        self.createdTs: float = time.time()
        self.version: int = 0

        # Runtime-local (e.g. game state, menu state)
        self.runtimeMemory: MemoryLayer = DictMemoryLayer("runtime")
        self.staticMemory: MemoryLayer = ReadOnlyMemoryLayer("static", {})

        # Kernel-level bottom layers (if kernel passed them)
        self.kernelBottom: list[MemoryLayer] = list(kernelMemoryLayers) if kernelMemoryLayers else []

        # Sessions owned by this runtime
        self.sessionsById: dict[str, Session] = {}

        # Main session
        self.mainSession: Session | None = None
        if createMainSession:
            self.mainSession = self.makeSession(kind=SessionKind("main"))

    def makeSession(
        self,
        *,
        kind: SessionKind,
        sessionId: str | None = None,
        ownerViewId: str | None = None,
        visibility: SessionVisibility = SessionVisibility("public"),
    ) -> Session:
        # Enforce single main session
        if kind == "main" and self.mainSession is not None:
            raise ValueError(f"Runtime '{self.id}' already has main session '{self.mainSession.id}'")
        
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
        if kind == "main":
            self.mainSession = sess
        self.version += 1
        return sess
    
    def getSession(self, sessionId: str) -> Session | None:
        return self.sessionsById.get(sessionId)
    
    def destroySession(self, sessionId: str) -> dict[str, Any]:
        # Protect main session
        if self.mainSession is not None and sessionId == self.mainSession.id:
            raise ValueError("Cannot destroy main session")
        sess = self.sessionsById.get(sessionId)
        if not sess:
            raise KeyError(f"Session '{sessionId}' does not exist")
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
            "mainSessionId": self.mainSession.id if self.mainSession else None,
            "sessions": {sid: sess.snapshot() for sid, sess in self.sessionsById.items()},
        }

    def destroy(self, *, keepMain: bool = False) -> None:
        for sessId, sess in list(self.sessionsById.items()):
            if not sess:
                continue
            if keepMain and self.mainSession and sessId == self.mainSession.id:
                continue
            try:
                sess.destroy()
            finally:
                self.sessionsById.pop(sessId, None)
        # If we removed the main session, forget the pointer
        if self.mainSession and self.mainSession.id not in self.sessionsById:
            self.mainSession = None
        self.version += 1
