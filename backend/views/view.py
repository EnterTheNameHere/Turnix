# backend/views/view.py
from __future__ import annotations
from typing import Any, Literal, TypedDict # pyright: ignore[reportShadowedImports]

from backend.app import state
from backend.core.ids import uuid_12
from backend.mods.frontend_index import makeFrontendIndex

__all__ = ["ViewSnapshot", "View", "Session"]



class ViewSnapshot(TypedDict):
    viewId: str
    template: str
    version: int
    state: dict[str, Any]
    sessions: dict[str, dict[str, Any]]



class View:
    """
    Backend representation of a single frontend instance (Electron's browser page/C# avatar/etc.)
    - Authoritative state
    - Owns sessions: one immortal "main", plus hidden/temporary when needed
    """
    def __init__(self, *, template: str | None = None, viewId: str | None = None):
        self.id: str = viewId or uuid_12("v_")
        self.template: str = template or "main_menu"
        self.state: dict[str, Any] = {
            "mods": {
                "frontend": makeFrontendIndex(),
                "backend": {
                    "loaded": state.PYMODS_LOADED,
                    "failed": state.PYMODS_FAILED, 
                },
            }
        }
        self.version = 0
        self.sessions: dict[str, Session] = {}
        self.mainSession = self.createSession("main")
    
    def destroy(self) -> None:
        pass

    def createSession(self, kind: Literal["main", "hidden", "temporary"], sessionId: str | None = None) -> Session:
        if kind == "main":
            for sess in self.sessions.values():
                if sess.kind == "main":
                    return sess
        # TODO: What if session exists?
        sess = Session(kind=kind, sessionId=sessionId)
        self.sessions[sess.id] = sess
        return sess
    
    def destroySession(self, sessionId: str) -> dict[str, Any]:
        sess = self.sessions.get(sessionId)
        if not sess:
            raise KeyError(f"session '{sessionId}' does not exist")
        sess.destroy()
        del self.sessions[sess.id]
        self.version += 1
        return {"ok": True, "version": self.version}

    def getSession(self, sessionId: str) -> Session | None:
        return self.sessions.get(sessionId)
    
    def setTemplate(self, template: str) -> dict[str, Any]:
        if not template:
            raise ValueError("template must be non-empty")
        self.template = template
        self.version += 1
        return {"ok": True, "version": self.version}

    def getState(self) -> dict[str, Any]:
        return {"viewId": self.id, "template": self.template, "state": self.state, "version": self.version}

    def setState(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("patch must be an object")
        self.state.update(patch)
        self.version += 1
        return {"ok": True, "version": self.version}

    def patchState(self, patch: dict[str, Any]) -> int:
        self.state.update(patch or {})
        self.version += 1
        return self.version
    
    def snapshot(self) -> ViewSnapshot:
        return {
            "viewId": self.id,
            "template": self.template,
            "version": self.version,
            "state": self.state,
            "sessions": {sessId: sess.snapshot() for sessId, sess in self.sessions.items()},
        }



class Session:
    """
    Session (main/hidden/temporary) owned by a View.
    Distinct from the RPC Session (transport/handshake)
    """
    def __init__(self, kind: Literal["main", "hidden", "temporary"], sessionId: str | None = None) -> None:
        self.kind = kind
        self.id = sessionId or uuid_12("s_")
        self.version = 0
        self.state: dict[str, Any] = {} # Authoritative on backend
        self.objects: dict[str, Any] = {}
        self.chat = {
            "threadId": uuid_12("t_"),
            "order": [],          # [oid]
            "headers": {},        # oid -> {role, ts, preview}
            "messages": {},       # oid -> {role, content?, status, ts, runId?}
            "historyPolicy": {
                "strategy": "window",
                "userTail": 6,
                "assistantTail": 6,
                "maxTokens": 2048,
                "alwaysIncludeSystem": True,
            },
            "subs": set() # correlatesTo ids of chat.thread@1 subscribers
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "version": self.version,
            "state": self.state,
            "objects": list(self.objects.keys()),
        }
    
    def destroy(self) -> None:
        pass
