# backend/runtimes/instance.py
from __future__ import annotations
import logging
import re
import time
from pathlib import Path
from typing import Any

from backend.app.globals import getRootsService, getTracer
from backend.core.ids import uuid_12
from backend.memory.memory_layer import (
    DictMemoryLayer,
    ReadOnlyMemoryLayer,
    MemoryLayer,
)
from backend.sessions.session import Session, SessionKind, SessionVisibility

__all__ = ["AppInstance"]

logger = logging.getLogger(__name__)



class AppInstance:
    """
    Represents an active, in-memory instance of an application.
    
    - Manages sessions (creation, lookup, main session tracking)
    - Holds override memory layers and kernel-provided layers
    - Knows which packs/mods are allowed for this instance
    - Provides the save directory for persistence
    
    Note:
        An AppInstance is often derived from an appPack, but can exist
        without one. The appPackId identifies the source pack if present.
        
        saveBaseDirectory is used to override the default save directory.
    """
    def __init__(
        self,
        *,
        appPackId: str,
        appInstanceId: str | None = None,
        saveBaseDirectory: Path | str | None = None, # overrides save directory; uses ContentRootsService otherwise
        kernelMemoryLayers: list[MemoryLayer] | None = None,
        createMainSession: bool = True,
    ) -> None:
        self.appPackId = appPackId.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.@-]+", self.appPackId):
            raise ValueError("appPackId contains invalid characters")
        if not self.appPackId:
            raise ValueError("appPackId must be a non-empty string")
        self.id = appInstanceId or uuid_12("appInstanceId_")
        
        if saveBaseDirectory is None:
            baseSaves = getRootsService().getWriteDir("saves")
            self.saveRoot: Path = (Path(baseSaves) / self.appPackId / self.id).resolve()
        else:
            baseSaves = Path(saveBaseDirectory)
            # If the supplied base already points at this appInstance's save directory, reuse it
            # as-is to avoid duplicating the appPack/appInstance path segments.
            if baseSaves.name == self.id and baseSaves.parent.name == self.appPackId:
                self.saveRoot = baseSaves
            else:
                self.saveRoot = (baseSaves / self.appPackId / self.id).resolve()
        self.saveRoot.mkdir(parents=True, exist_ok=True)
        
        self.createdTs: float = time.time()
        self.version: int = 0
        self._traceSpan = None
        
        tracer = getTracer()
        tracer.updateTraceContext({
            "appInstanceId": self.id,
        })
        try:
            self._traceSpan = tracer.startSpan(
                "appInstance.lifecycle",
                attrs={"appPackId": self.appPackId},
                tags=["appInstance"],
                contextOverrides={
                    "appInstanceId": self.id,
                },
            )
            tracer.traceEvent(
                "appInstance.create",
                attrs={
                    "appPackId": self.appPackId,
                    "appInstanceId": self.id,
                },
                level="info",
                tags=["appInstance"],
                span=self._traceSpan,
            )
        except Exception:
            # Tracing must never break appInstance construction.
            self._traceSpan = None

        # Runtime-local (e.g. game state, menu state)
        self.runtimeMemory: MemoryLayer = DictMemoryLayer("runtime")
        self.staticMemory: MemoryLayer = ReadOnlyMemoryLayer("static", {})

        # Kernel-level bottom layers (if kernel passed them)
        self.kernelBottom: list[MemoryLayer] = list(kernelMemoryLayers) if kernelMemoryLayers else []

        # Sessions owned by this appInstance
        self.sessionsById: dict[str, Session] = {}

        # Main session
        self.mainSession: Session | None = None
        if createMainSession:
            self.mainSession = self.makeSession(kind=SessionKind.MAIN)
        
        # Packs allowed to be used by this appInstance - set later, empty by default
        self.allowedPacks: set[str] = set()
        
        # Information about python mods
        self.backendPacksLoaded: list[dict[str, Any]] = []
        self.backendPacksFailed: list[dict[str, Any]] = []

    def makeSession(
        self,
        *,
        kind: SessionKind,
        sessionId: str | None = None,
        ownerViewId: str | None = None,
        visibility: SessionVisibility = SessionVisibility.PUBLIC,
    ) -> Session:
        # Enforce single main session
        if kind == SessionKind.MAIN and self.mainSession is not None:
            raise ValueError(f"appInstance '{self.id}' already has main session '{self.mainSession.id}'")
        
        # Order: higher to lower; kernel is last = lowest priority (gets accessed as last)
        bottom: list[MemoryLayer] = [
            self.runtimeMemory,
            self.staticMemory,
            *self.kernelBottom,
        ]
        
        session = Session(
            kind=kind,
            sessionId=sessionId,
            ownerViewId=ownerViewId,
            visibility=visibility,
            sharedBottomLayers=bottom,
            savePath=self.saveRoot,
        )

        self.sessionsById[session.id] = session
        if kind == SessionKind.MAIN:
            self.mainSession = session
        self.version += 1
        return session
    
    def getSession(self, sessionId: str) -> Session | None:
        return self.sessionsById.get(sessionId)
    
    def destroySession(self, sessionId: str) -> dict[str, Any]:
        # Protect main session
        if self.mainSession is not None and sessionId == self.mainSession.id:
            raise ValueError("Cannot destroy main session")
        session = self.sessionsById.get(sessionId)
        if not session:
            raise KeyError(f"Session '{sessionId}' does not exist")
        session.destroy()
        del self.sessionsById[sessionId]
        self.version += 1
        return {"ok": True, "version": self.version}

    def listSessions(self, *, kind: SessionKind | str | None = None) -> list[str]:
        """Return session ids, optionally filtered by kind."""
        if kind is None:
            return sorted(self.sessionsById.keys())
        # Normalize filter to SessionKind
        kindNorm = SessionKind(kind) if isinstance(kind, str) else kind
        return sorted([sessionId for sessionId, session in self.sessionsById.items() if session.kind == kindNorm])

    def setAllowedPacks(self, allowedPacks: set[str]) -> None:
        """Set the allowed packs for this appInstance instance."""
        self.allowedPacks = allowedPacks
    
    def getAllowedPacks(self) -> set[str]:
        """Return the allowed packs for this appInstance instance."""
        return self.allowedPacks
    
    def snapshot(self) -> dict[str, object]:
        return {
            "appPackId": self.appPackId,
            "appInstanceId": self.id,
            "saveRoot": str(self.saveRoot),
            "version": self.version,
            "createdTs": self.createdTs,
            "mainSessionId": self.mainSession.id if self.mainSession else None,
            "sessions": {sid: session.snapshot() for sid, session in self.sessionsById.items()},
        }
    
    @classmethod
    def fromSnapshot(
        cls,
        snapshot: dict[str, Any],
        *,
        appPackId: str,
        saveBaseDirectory: Path | str | None = None,
        kernelMemoryLayers: list[MemoryLayer] | None = None,
    ) -> AppInstance:
        """
        Reconstruct a AppInstance from a snapshot (pure data, no I/O).
        You must pass appPackId and optional saveBaseDirectory to resolve paths.
        """
        instance = cls(
            appPackId=appPackId,
            appInstanceId=snapshot.get("appInstanceId"),
            saveBaseDirectory=saveBaseDirectory,
            kernelMemoryLayers=kernelMemoryLayers,
            createMainSession=False,
        )
        
        instance.createdTs = float(snapshot.get("createdTs", time.time()))
        instance.version = int(snapshot.get("version", 0))
        
        sharedBottomLayers: list[MemoryLayer] = [
            instance.runtimeMemory,
            instance.staticMemory,
            *instance.kernelBottom,
        ]
        
        # Restore sessions
        sessionsData: dict[str, dict[str, Any]] = snapshot.get("sessions", {})
        for sessionId, sessionSnapshot in sessionsData.items():
            session = Session.fromSnapshot(
                sessionSnapshot,
                sharedBottomLayers=sharedBottomLayers,
                savePath=instance.saveRoot,
            )
            instance.sessionsById[sessionId] = session
        
        # Main session
        mainSessionId = snapshot.get("mainSessionId")
        if isinstance(mainSessionId, str) and mainSessionId in instance.sessionsById:
            instance.mainSession = instance.sessionsById[mainSessionId]
        elif instance.sessionsById:
            logger.debug("No mainSessionId in snapshot. Selecting first deterministically")
            instance.mainSession = instance.sessionsById[sorted(instance.sessionsById.keys())[0]]
        
        return instance

    def destroy(self, *, keepMain: bool = False) -> None:
        for sessionId, session in list(self.sessionsById.items()):
            if not session:
                continue
            if keepMain and self.mainSession and sessionId == self.mainSession.id:
                continue
            try:
                session.destroy()
            finally:
                self.sessionsById.pop(sessionId, None)
        # If we removed the main session, forget the pointer
        if self.mainSession and self.mainSession.id not in self.sessionsById:
            self.mainSession = None
        self.version += 1
        
        span = getattr(self, "_traceSpan", None)
        if span is not None:
            tracer = getTracer()
            try:
                tracer.traceEvent(
                    "appInstance.destroy",
                    attrs={
                        "keepMain": keepMain,
                        "appInstanceId": self.id,
                    },
                    level="info",
                    tags=["appInstance"],
                    span=span,
                )
                tracer.endSpan(
                    span,
                    status="ok",
                    tags=["appInstance"],
                    attrs={
                        "destroyedTs": time.time(),
                        "keepMain": keepMain,
                    },
                )
            except Exception:
                # Tracing errors must not interfere with destruction
                pass
            self._traceSpan = None
