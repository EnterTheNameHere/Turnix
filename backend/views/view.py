# backend/views/view.py
from __future__ import annotations
from typing import Any, TypedDict

from backend.app.globals import getActiveRuntime, getTracer, getActiveAppPack
from backend.content.packs import PackResolver
from backend.core.ids import uuid_12
from backend.core.tracing import TraceSpan
from backend.mods.frontend_index import makeFrontendIndex
from backend.mods.discover import scanMods

__all__ = ["ViewSnapshot", "View"]



class ViewSnapshot(TypedDict):
    viewId: str
    viewKind: str
    appPackId: str
    version: int
    state: dict[str, Any]
    attachedSessionIds: list[str]



class View:
    """
    Backend representation of a single frontend instance (Electron's browser page/C# avatar/etc.)
    - Authoritative *UI* state.
    - Attaches to world Session(s) owned by GameRealm (main, temporary, hidden).
    """
    def __init__(
        self,
        *,
        appPackId: str | None = None,
        viewId: str | None = None,
        viewKind: str = "main",
    ):
        self.id: str = viewId or uuid_12("view_")
        self.viewKind: str = viewKind or "main"
        self.appPackId: str = appPackId or "turnix@main_menu"
        self.frontendModsIndex: dict[str, Any] = {}
        
        self._traceSpan: TraceSpan | None = None
        tracer = getTracer()
        span = tracer.startSpan(
            "view.lifecycle",
            attrs={
                "viewId": self.id,
                "appPackId": self.appPackId,
            },
            level="info",
            tags=["view"],
            contextOverrides={
                "viewId": self.id,
                "viewKind": self.viewKind,
            },
        )
        self._traceSpan = span
        
        activeInstance = getActiveRuntime()
        self.state: dict[str, Any] = {
            "viewKind": self.viewKind,
            "mods": {
                "frontend": self.frontendModsIndex,
                "backend": {
                    "loaded": activeInstance.backendPacksLoaded,
                    "failed": activeInstance.backendPacksLoaded, 
                },
            }
        }
        self.version: int = 0
        self.attachedSessionIds: set[str] = set()
        
        self.refreshFrontendIndex()
        
    def destroy(self) -> None:
        tracer = getTracer()
        if self._traceSpan is not None:
            try:
                tracer.endSpan(
                    self._traceSpan,
                    status="ok",
                    level="info",
                    tags=["view"],
                    attrs={
                        "viewId": self.id,
                        "appPackId": self.appPackId,
                        "viewKind": self.viewKind,
                        "attachedSessionIds": sorted(self.attachedSessionIds),
                        "version": self.version,
                    },
                )
            except Exception:
                # Tracing must not break destroy().
                pass
            self._traceSpan = None

    def attachSession(self, sessionId: str) -> dict[str, Any]:
        self.attachedSessionIds.add(sessionId)
        self.version += 1
        
        tracer = getTracer()
        if self._traceSpan is not None:
            tracer.traceEvent(
                "view.attachSession",
                attrs={
                    "viewId": self.id,
                    "viewKind": self.viewKind,
                    "sessionId": sessionId,
                    "version": self.version,
                },
                level="info",
                tags=["view"],
                span=self._traceSpan,
            )
        
        return {"ok": True, "version": self.version}
    
    def detachSession(self, sessionId: str) -> dict[str, Any]:
        self.attachedSessionIds.discard(sessionId)
        self.version += 1
        
        tracer = getTracer()
        if self._traceSpan is not None:
            tracer.traceEvent(
                "view.detachSession",
                attrs={
                    "viewId": self.id,
                    "viewKind": self.viewKind,
                    "sessionId": sessionId,
                    "version": self.version,
                },
                level="info",
                tags=["view"],
                span=self._traceSpan,
            )
        
        return {"ok": True, "version": self.version}

    def isAttached(self, sessionId: str) -> bool:
        return sessionId in self.attachedSessionIds

    def setAppPackId(self, appPackId: str) -> dict[str, Any]:
        appPackId = (appPackId or "").strip()
        if not appPackId:
            raise ValueError("appPackId must be non-empty")
        self.appPackId = appPackId
        self.version += 1
        
        tracer = getTracer()
        if self._traceSpan is not None:
            tracer.traceEvent(
                "view.setAppPackId",
                attrs={
                    "viewId": self.id,
                    "viewKind": self.viewKind,
                    "appPackId": self.appPackId,
                    "version": self.version,
                },
                level="info",
                tags=["view"],
                span=self._traceSpan,
            )
        
        return {"ok": True, "version": self.version}

    def getState(self) -> dict[str, Any]:
        return {
            "viewId": self.id,
            "appPackId": self.appPackId,
            "viewKind": self.viewKind,
            "state": self.state,
            "version": self.version,
        }

    def setState(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("patch must be an object")
        self.state.update(patch)
        self.version += 1
        
        tracer = getTracer()
        if self._traceSpan is not None:
            tracer.traceEvent(
                "view.setState",
                attrs={
                    "viewId": self.id,
                    "keys": sorted(patch.keys()),
                    "version": self.version,
                },
                level="debug",
                tags=["view"],
                span=self._traceSpan,
            )
        
        return {"ok": True, "version": self.version}

    def patchState(self, patch: dict[str, Any]) -> int:
        self.state.update(patch or {})
        self.version += 1
        
        tracer = getTracer()
        if self._traceSpan is not None:
            tracer.traceEvent(
                "view.patchState",
                attrs={
                    "viewId": self.id,
                    "keys": sorted(patch.keys()) if isinstance(patch, dict) else [],
                    "version": self.version,
                },
                level="debug",
                tags=["view"],
                span=self._traceSpan,
            )
        
        return self.version
    
    def snapshot(self) -> ViewSnapshot:
        return {
            "viewId": self.id,
            "appPackId": self.appPackId,
            "viewKind": self.viewKind,
            "version": self.version,
            "state": self.state,
            "attachedSessionIds": sorted(self.attachedSessionIds),
        }

    def refreshFrontendIndex(self) -> dict[str, Any]:
        """
        Rebuild and store the frontend mod manifest index.
        """
        activeRuntime = getActiveRuntime()
        appPack = getActiveAppPack()
        resolver = PackResolver()
        if appPack is not None:
            viewPack = resolver.resolveViewPackForApp(appPack, self.viewKind)
        if viewPack is None:
            viewPack = resolver.resolveViewPack(self.viewKind)
        extraRoots = viewPack.rootDir if viewPack is not None else None
        found = scanMods(
            allowedIds=activeRuntime.allowedPacks,
            appPack=getActiveAppPack(),
            saveRoot=activeRuntime.saveRoot,
            extraRoots=[extraRoots] if extraRoots is not None else None
        )
        self.frontendModsIndex = makeFrontendIndex(found, viewId=self.id)
        self.state.setdefault("mods", {})["frontend"] = self.frontendModsIndex
        self.version += 1
        
        tracer = getTracer()
        if self._traceSpan is not None:
            tracer.traceEvent(
                "view.refreshFrontendIndex",
                attrs={
                    "viewId": self.id,
                    "foundCount": len(found),
                    "version": self.version,
                },
                level="info",
                tags=["view"],
                span=self._traceSpan,
            )
        
        return {"ok": True, "version": self.version}
