# backend/views/view.py
from __future__ import annotations
from typing import Any, Literal, TypedDict # pyright: ignore[reportShadowedImports]

from backend.app import state
from backend.core.ids import uuid_12
from backend.game.realm import GameRealm
from backend.mods.frontend_index import makeFrontendIndex
from backend.sessions.session import Session

__all__ = ["ViewSnapshot", "View"]



class ViewSnapshot(TypedDict):
    viewId: str
    template: str
    version: int
    state: dict[str, Any]
    attachedSessionIds: list[str]



class View:
    """
    Backend representation of a single frontend instance (Electron's browser page/C# avatar/etc.)
    - Authoritative *UI* state.
    - Attaches to world Session(s) owned by GameRealm (main, temporary, hidden).
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
        self.version: int = 0
        self.attachedSessionIds: set[str] = set()
    
    def destroy(self) -> None:
        pass

    def attachMainSession(self, realm: GameRealm) -> str:
        self.attachedSessionIds.add(realm.mainSession.id)
        self.version += 1
        return realm.mainSession.id

    def attachSession(self, sessionId: str) -> dict[str, Any]:
        self.attachedSessionIds.add(sessionId)
        self.version += 1
        return {"ok": True, "version": self.version}
    
    def detachSession(self, sessionId: str) -> dict[str, Any]:
        self.attachedSessionIds.discard(sessionId)
        self.version += 1
        return {"ok": True, "version": self.version}

    def isAttached(self, sessionId: str) -> bool:
        return sessionId in self.attachedSessionIds

    def setTemplate(self, template: str) -> dict[str, Any]:
        template = (template or "").strip()
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
            "attachedSessionIds": sorted(self.attachedSessionIds),
        }

    def resolveMainSession(self) -> Session:
        """
        Resolve the 'main' Session for this View:
          1) If a GameRealm exists, return it's main Session
          2) Else fallback to the AppShell's shell Session
        This MUST always succeed, otherwise the process state is undefined.
        """
        realm = state.GAME_REALM
        if realm is not None:
            return realm.mainSession
        
        # No GameRealm running, use AppShell
        shell = state.APP_SHELL
        if shell is not None:
            return shell.shellSession
        
        # If we got here, the app state is corrupted and the UI cannot function.
        from backend.core.errors import ReactorScramError
        raise ReactorScramError(
            "No main session available: neither GAME_REALM nor APP_SHELL is set."
            " The runtime cannot continue safely. And we don't want to continue unsafely."
            " Safety is our priority. Wait a moment... Oh, our safety officer has quit the job."
            " He is fleeing the ship. Oh sure, everybody is fleeing. You should see it - but the UI"
            " doesn't work, so you can't see anything. Oh well, it was nice knowing you. Ah, the water's here."
        )
