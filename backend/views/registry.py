# backend/views/registry.py
from __future__ import annotations
import secrets
from collections.abc import Mapping # pyright: ignore[reportShadowedImports] - one of our requirement ships typings extra, but Python 3.12 already includes them

from backend.views.view import View
from backend.core.ids import uuid_12



class ViewRegistry:
    def __init__(self):
        # viewId -> View
        self.viewsById: dict[str, View] = {}
        # clientId -> viewId
        self.bindingsByClientId: dict[str, str] = {}
        # (viewId, clientId) -> viewToken
        self.tokens: dict[tuple[str, str], str] = {}
    
    def _mintViewToken(self) -> str:
        return secrets.token_urlsafe(24)
    
    def ensureClientId(self, reqCookies: Mapping[str, str] | None) -> str:
        cookies = reqCookies or {}
        cid = cookies.get("clientId")
        if isinstance(cid, str) and cid.strip():
            return cid
        # New or unknown client: mint a fresh clientId
        return uuid_12("c_")

    def getOrCreateViewForClient(self, clientId: str) -> tuple[View, str]:
        """
        Returns (view, viewTokenForThisClient)
        Creates a new View if none bound yet; otherwise returns existing + (rotated) token.
        """
        if not isinstance(clientId, str) or not clientId.strip():
            raise ValueError(f"'clientId' must be non-empty string; got {repr(clientId)}")

        # Race begun
        existingId = self.bindingsByClientId.get(clientId)
        if existingId:
            if existingId not in self.viewsById:
                # Invariant violation: binding points to missing view, which shouldn't happen
                raise LookupError(f"View {existingId!r} not found!")
            view = self.viewsById[existingId]
        else:
            # Optimistically create a new view, then atomically "claim" the binding.
            newView = View()
            # NOTE: In case of needing more hardening, use asyncio.Lock
            boundId = self.bindingsByClientId.setdefault(clientId, newView.id)
            if boundId == newView.id:
                # We won the race - store the new view
                self.viewsById[newView.id] = newView
                view = newView
            else:
                # Lost the race - discard the new view, use the already bound one
                # (assert it exists or treat it as invariant violation)
                try:
                    view = self.viewsById[boundId]
                except KeyError:
                    raise LookupError(f"View {boundId!r} not found!")
        
        key = (view.id, clientId)
        token = self._mintViewToken()
        self.tokens[key] = token
        return view, token
    
    # TODO: What if someone tries forging minting token for existing client and view?
    def issueToken(self, viewId: str, clientId: str) -> str:
        if not isinstance(viewId, str) or not viewId.strip():
            raise ValueError(f"'viewId' must be non-empty string; got {repr(viewId)}")
        if not isinstance(clientId, str) or not clientId.strip():
            raise ValueError(f"'clientId' must be non-empty string; got {repr(clientId)}")

        key = (viewId, clientId)
        token = self._mintViewToken()
        self.tokens[key] = token
        return token
    
    def revokeToken(self, viewId: str, clientId: str) -> None:
        """Removes the token for (viewId, clientId) if present."""
        self.tokens.pop((viewId, clientId), None)

    def validateToken(self, viewId: str, clientId: str, token: str) -> bool:
        if not viewId or not clientId or not token:
            return False
        
        key = (viewId, clientId)
        stored = self.tokens.get(key)
        # Timing-safe comparison
        return stored is not None and secrets.compare_digest(stored, token)
    
    def getViewById(self, viewId: str) -> View | None:
        if not isinstance(viewId, str) or not viewId.strip():
            raise ValueError(f"'viewId' must be non-empty string; got {repr(viewId)}")
        
        return self.viewsById.get(viewId)
    
    def bindClientToView(self, clientId: str, viewId: str) -> None:
        if not isinstance(viewId, str) or not viewId.strip():
            raise ValueError(f"'viewId' must be non-empty string; got {repr(viewId)}")
        if not isinstance(clientId, str) or not clientId.strip():
            raise ValueError(f"'clientId' must be non-empty string; got {repr(clientId)}")

        oldViewId = self.bindingsByClientId.get(clientId)
        if oldViewId and oldViewId != viewId:
            self.tokens.pop((oldViewId, clientId), None)
        
        self.bindingsByClientId[clientId] = viewId
        if viewId not in self.viewsById:
            self.viewsById[viewId] = View(viewId=viewId)
    
    def unbindClient(self, clientId: str) -> bool:
        """Removes the clientId binding and any token associated with it."""
        viewId = self.bindingsByClientId.pop(clientId, None)
        if viewId:
            self.tokens.pop((viewId, clientId), None)
            return True
        return False

    def destroyView(self, viewId: str) -> bool:
        view = self.viewsById.pop(viewId, None)
        if not view:
            return False
        
        # Unbind all clients pointing to this view (with or without a token)
        for vid, cid in list(self.bindingsByClientId.items()):
            if vid == viewId:
                self.bindingsByClientId.pop(cid, None)
                self.tokens.pop((viewId, cid), None)
        return True

viewRegistry = ViewRegistry()
