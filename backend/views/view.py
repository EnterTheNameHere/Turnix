# backend/views/view.py
from __future__ import annotations
from typing import Any, TypedDict

from backend.app import state
from backend.app.globals import getTracer
from backend.core.ids import uuid_12
from backend.core.tracing import TraceSpan
from backend.mods.frontend_index import makeFrontendIndex
from backend.mods.discover import scanMods, scanModsForMount

__all__ = ["ViewSnapshot", "View"]



class ViewSnapshot(TypedDict):
    viewId: str
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
    def __init__(self, *, appPackId: str | None = None, viewId: str | None = None):
        self.id: str = viewId or uuid_12("view_")
        self.appPackId: str = appPackId or "turnix@main_menu"
        
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
            },
        )
        self._traceSpan = span
        
        # Build initial frontend mod index from default (unmounted) roots.
        # If/when a runtime supplies a custom mountId, call refreshFrontendIndex(mountId=...) later. 
        frontendIndex = makeFrontendIndex(
            scanMods(),
            base="/mods/load",
            mountId=None,
        )
        self.state: dict[str, Any] = {
            "mods": {
                "frontend": frontendIndex,
                "backend": {
                    "loaded": state.PYMODS_LOADED,
                    "failed": state.PYMODS_FAILED, 
                },
            }
        }
        self.version: int = 0
        self.attachedSessionIds: set[str] = set()
    
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
            "version": self.version,
            "state": self.state,
            "attachedSessionIds": sorted(self.attachedSessionIds),
        }

    def refreshFrontendIndex(self, *, mountId: str | None = None) -> dict[str, Any]:
        """
        Rebuild and store the frontend mod manifest index.
        - mountId=None → default roots (/mods/load)
        - mountId="xzy" → mounted roots /mods/{xzy}/load
        """
        if mountId:
            base = f"/mods/{mountId}/load"
            found = scanModsForMount(mountId)
            index = makeFrontendIndex(found, base=base, mountId=mountId)
        else:
            found = scanMods()
            index = makeFrontendIndex(found, base="/mods/load", mountId=None)
        self.state.setdefault("mods", {})["frontend"] = index
        self.version += 1
        
        tracer = getTracer()
        if self._traceSpan is not None:
            tracer.traceEvent(
                "view.refreshFrontendIndex",
                attrs={
                    "viewId": self.id,
                    "mountId": mountId,
                    "foundCount": len(found),
                    "version": self.version,
                },
                level="info",
                tags=["view"],
                span=self._traceSpan,
            )
        
        return {"ok": True, "version": self.version}
