# backend/views/manager.py
from __future__ import annotations
from fastapi import WebSocket

from backend.views.view import View
from backend.views.registry import viewRegistry

__all__ = ["ViewManager", "viewManager"]



class ViewManager:
    """
    Tracks which View a WebSocket is attached to and per-view subscription membership
    for streaming fanout.
    """
    def __init__(self):
        # Note: WebSocket objects are hashable, so they can be used as dict keys.
        self._viewIdByWs: dict[WebSocket, str] = {}
        self._wsByViewId: dict[str, set[WebSocket]] = {}

    def bind(self, *, ws: WebSocket, view: View) -> None:
        if not isinstance(ws, WebSocket):
            raise TypeError("Expected a WebSocket")
        if not isinstance(view, View):
            raise TypeError("Expected a View")

        oldViewId = self._viewIdByWs.get(ws)
        if oldViewId and oldViewId != view.id:
            # Detach from old view set (if present)
            oldSet = self._wsByViewId.get(oldViewId)
            if oldSet:
                oldSet.discard(ws)
                if not oldSet:
                    self._wsByViewId.pop(oldViewId, None)

        # Attach to new view
        self._viewIdByWs[ws] = view.id
        self._wsByViewId.setdefault(view.id, set()).add(ws)
    
    def getViewForWs(self, ws: WebSocket) -> View | None:
        if not isinstance(ws, WebSocket):
            raise TypeError("Expected a WebSocket")
        
        viewId = self._viewIdByWs.get(ws)
        return viewRegistry.getViewById(viewId) if viewId else None

    def removeViewForWs(self, ws: WebSocket) -> None:
        """
        Call on disconnect: unbind the socket from its view and
        drop empty view sets.
        """
        if not isinstance(ws, WebSocket):
            raise TypeError("Expected a WebSocket")

        viewId = self._viewIdByWs.pop(ws, None)
        if viewId:
            wsSet = self._wsByViewId.get(viewId)
            if wsSet:
                wsSet.discard(ws)
                if not wsSet:
                    self._wsByViewId.pop(viewId, None)
    
    def unbindAllForView(self, viewId: str) -> int:
        """
        Unbind all sockets currently attached to viewId.
        Returns the number of sockets unbound.
        """
        wsSet = self._wsByViewId.pop(viewId, None)
        if not wsSet:
            return 0
        count = 0
        for ws in list(wsSet):
            if self._viewIdByWs.pop(ws, None) == viewId:
                count += 1
        return count

    def socketsForView(self, viewId: str) -> set[WebSocket]:
        """
        Returns a snapshot set of sockets for a view (safe to iterate).
        The returned set is a copy and won't mutate internal state.
        """
        return set(self._wsByViewId.get(viewId, ()))

    def viewIds(self) -> set[str]:
        """
        Returns a snapshot set of all viewIds that currently have at least one socket.
        """
        return set(self._wsByViewId.keys())
    
    def socketsByViewSnapshot(self) -> dict[str, set[WebSocket]]:
        """
        Returns a snapshot mapping of viewId -> set(WebSocket).
        Each set is a copy; safe to iterate and will not mutate internal state.
        """
        return {viewId: set(sockSet) for viewId, sockSet in self._wsByViewId.items()}
    
    def iterViews(self):
        """
        Yield (viewId, socketsSnapshot) for each active view.
        socketsSnapshot is a copy (safe to iterate).
        """
        for viewId, sockSet in self._wsByViewId.items():
            yield viewId, set(sockSet)
    
    def iterAllSockets(self):
        """
        Yield (ws, viewId) pairs for all currently bound sockets.
        """
        for ws, viewId in self._viewIdByWs.items():
            yield ws, viewId



viewManager = ViewManager()
