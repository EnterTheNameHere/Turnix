# backend/game/realm.py
from __future__ import annotations
import time
from typing import Literal

from backend.core.ids import uuid_12
from backend.sessions.session import Session
from backend.core.schema_registry import SchemaRegistry
from backend.config.store import ConfigStore
from backend.config.providers import DefaultsProvider, FileProvider, RuntimeProvider, ViewProvider
from backend.app.paths import ROOT_DIR

__all__ = ["GameRealm"]



class GameRealm:
    """
    Authoritative world/runtime container.
    - Owns the main Session (global timeline)
    - Can create additional sessions (hidden/temp), optionally tagged with ownerViewId
    - View *attach to* sessions; they don't own main one.
    """
    def __init__(
            self,
            *,
            realmId: str | None = None,
            configRegistry: SchemaRegistry,
            globalConfigView: ConfigStore,
        ):
        self.id: str = realmId or uuid_12("realm_")
        self.version: int = 0
        self.createdTs: float = time.time()
        self.sessionsById: dict[str, Session] = {}

        self.config = self._initRealmConfig(configRegistry, globalConfigView)

        # Create the main (world-authoritative) session up-front.
        self.mainSession: Session = self.createSession(
            kind="main",
            sessionId=uuid_12("ms_"),
            ownerViewId=None,
            visibility="public",
        )
    
    def _initRealmConfig(self, reg: SchemaRegistry, globalConfig: ConfigStore) -> ConfigStore:
        validator = reg.getValidator("config", "realm")
        savePath = ROOT_DIR / "saves" / self.id / "config.json5"
        providers = [
            DefaultsProvider(path=str(ROOT_DIR / "assets" / "config" / "defaults" / "realm.json5")),  # Realm defaults
            # A "view provider" that reads from global (read-only)
            ViewProvider(globalConfig),   # Inhering global values as a lower layer
            FileProvider(path=str(savePath), readOnly=False),
            RuntimeProvider(),
        ]
        return ConfigStore(namespace="config:realm", validator=validator, providers=providers)

    def createSession(
            self,
            *,
            kind: Literal["main", "hidden", "temporary"],
            sessionId: str | None = None,
            ownerViewId: str | None = None,
            visibility: Literal["public", "private"] = "public",
    ) -> Session:
        # Reuse existing when caller provides an existing sessionId,
        # but ensure the requested kind matches the existing kind.
        if sessionId and sessionId in self.sessionsById:
            existing = self.sessionsById[sessionId]
            if existing.kind != kind:
                raise ValueError(f"session '{sessionId}' exists, but has kind='{existing.kind}', not '{kind}'!")
            return existing
        if kind == "main":
            # Ensure singleton mainSession. Reuse if exists.
            for session in self.sessionsById.values():
                if session.kind == "main":
                    return session
        
        sess = Session(
            kind=kind,
            sessionId=sessionId,
            ownerViewId=ownerViewId,
            visibility=visibility,
        )
        self.sessionsById[sess.id] = sess
        #sessionRegistry.register(sess)
        self.version += 1
        return sess

    def getSession(self, sessionId: str) -> Session | None:
        return self.sessionsById.get(sessionId)
    
    def hasSession(self, sessionId: str) -> bool:
        return sessionId in self.sessionsById

    def destroySession(self, sessionId: str) -> dict[str, object]:
        sess = self.sessionsById.get(sessionId)
        if not sess:
            raise KeyError(f"session '{sessionId}' does not exist")
        if sess.kind == "main":
            raise ValueError("cannot destroy main session")
        sess.destroy()
        del self.sessionsById[sessionId]
        self.version += 1
        return {"ok": True, "version": self.version}
    
    def snapshot(self) -> dict[str, object]:
        return {
            "realmId": self.id,
            "version": self.version,
            "createdTs": self.createdTs,
            "sessions": {sid: sess.snapshot() for sid, sess in self.sessionsById.items()},
            "mainSessionId": self.mainSession.id,
        }

    def listSessions(self, *, kind: str | None = None) -> list[str]:
        """Return session ids, optionally filtered by kind."""
        if kind is None:
            return sorted(self.sessionsById.keys())
        return sorted([sid for sid, sess in self.sessionsById.items() if sess.kind == kind])

    def destroy(self, *, keepMain: bool = True) -> None:
        """Tear down all sessions (except main by default)."""
        ids = self.listSessions()
        for sid in ids:
            if sid == self.mainSession.id:
                if keepMain:
                    continue
                # Special case: allow main teardown here (destroy session forbids it)
                try:
                    self.mainSession.destroy()
                finally:
                    self.sessionsById.pop(sid, None)
                    self.version += 1
                continue
            try:
                self.destroySession(sid)
            except Exception:
                # Best-effort; realm teardown shouldn't explode due to one bad session
                pass
