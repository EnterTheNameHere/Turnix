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
        saveBaseDirectory: Path | str | None = None, # overrides save directory; uses RootsService otherwise
        kernelMemoryLayers: list[MemoryLayer] | None = None,
        createMainSession: bool = True,
    ) -> None:
        self.appPackId = appPackId.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.@-]+", self.appPackId):
            raise ValueError("appPackId contains invalid characters")
        if not self.appPackId:
            raise ValueError("appPackId must be a non-empty string")
        self.id = runtimeInstanceId or uuid_12("runtimeInstance_")
        
        if saveBaseDirectory is None:
            baseSaves = getRootsService().getWriteDir("saves")
            self.saveRoot: Path = (Path(baseSaves) / self.appPackId / self.id).resolve()
        else:
            baseSaves = Path(saveBaseDirectory)
            # If the supplied base already points at this runtime's save directory, reuse it
            # as-is to avoid duplicating the appPack/runtimeInstance path segments.
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
            "runtimeInstanceId": self.id,
        })
        try:
            self._traceSpan = tracer.startSpan(
                "runtime.lifecycle",
                attrs={"appPackId": self.appPackId},
                tags=["runtime"],
                contextOverrides={
                    "runtimeInstanceId": self.id,
                },
            )
            tracer.traceEvent(
                "runtime.create",
                attrs={
                    "appPackId": self.appPackId,
                    "runtimeInstanceId": self.id,
                },
                level="info",
                tags=["runtime"],
                span=self._traceSpan,
            )
        except Exception:
            # Tracing must never break runtime construction.
            self._traceSpan = None

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
            self.mainSession = self.makeSession(kind=SessionKind.MAIN)
        
        # Packs allowed to be used by this runtime - set later, empty by default
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
            raise ValueError(f"Runtime '{self.id}' already has main session '{self.mainSession.id}'")
        
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
        """Set the allowed packs for this runtime instance."""
        self.allowedPacks = allowedPacks
    
    def getAllowedPacks(self) -> set[str]:
        """Return the allowed packs for this runtime instance."""
        return self.allowedPacks
    
    def snapshot(self) -> dict[str, object]:
        return {
            "appPackId": self.appPackId,
            "runtimeInstanceId": self.id,
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
    ) -> RuntimeInstance:
        """
        Reconstruct a RuntimeInstance from a snapshot (pure data, no I/O).
        You must pass appPackId and optional saveBaseDirectory to resolve paths.
        """
        instance = cls(
            appPackId=appPackId,
            runtimeInstanceId=snapshot.get("runtimeInstanceId"),
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
                    "runtime.destroy",
                    attrs={
                        "keepMain": keepMain,
                        "runtimeInstanceId": self.id,
                    },
                    level="info",
                    tags=["runtime"],
                    span=span,
                )
                tracer.endSpan(
                    span,
                    status="ok",
                    tags=["runtime"],
                    attrs={
                        "destroyedTs": time.time(),
                        "keepMain": keepMain,
                    },
                )
            except Exception:
                # Tracing errors must not interfere with destruction
                pass
            self._traceSpan = None
