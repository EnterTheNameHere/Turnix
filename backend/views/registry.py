# backend/views/registry.py
from __future__ import annotations
import secrets
from collections.abc import Mapping # pyright: ignore[reportShadowedImports] - one of our requirement ships typings extra, but Python 3.12 already includes them

from backend.app.globals import getTracer
from backend.views.view import View
from backend.core.ids import uuid_12



class ViewRegistry:
    def __init__(self):
        # viewId -> View
        self.viewsById: dict[str, View] = {}
        # (clientId, viewKind) -> viewId
        self.bindingsByClientAndViewKindKey: dict[tuple[str, str], str] = {}
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
        clientId = uuid_12("c_")
        tracer = getTracer()
        tracer.traceEvent(
            "viewRegistry.ensureClientId",
            attrs={"clientId": clientId},
            level="debug",
            tags=["viewRegistry"],
        )
        return clientId

    def getOrCreateViewForClient(self, clientId: str, *, viewKind: str = "main") -> tuple[View, str]:
        """
        Returns (view, viewTokenForThisClient)
        Creates a new View if none bound for this (clientId, viewKind); otherwise returns existing + (rotated) token.
        """
        if not isinstance(clientId, str) or not clientId.strip():
            raise ValueError(f"'clientId' must be non-empty string; got {repr(clientId)}")

        tracer = getTracer()
        createdNew = False
        
        viewKind = (viewKind or "main").strip() or "main"
        key = (clientId, viewKind)
        
        # Race begun
        existingId = self.bindingsByClientAndViewKindKey.get(key)
        if existingId:
            if existingId not in self.viewsById:
                # Invariant violation: binding points to missing view, which shouldn't happen
                raise LookupError(f"View {existingId!r} not found!")
            view = self.viewsById[existingId]
        else:
            # Optimistically create a new view, then atomically "claim" the binding.
            newView = View(viewKind=viewKind)
            # NOTE: In case of needing more hardening, use asyncio.Lock
            boundId = self.bindingsByClientAndViewKindKey.setdefault(key, newView.id)
            if boundId == newView.id:
                # We won the race - store the new view
                self.viewsById[newView.id] = newView
                view = newView
                createdNew = True
            else:
                # Lost the race - discard the new view, use the already bound one
                # (assert it exists or treat it as invariant violation)
                try:
                    view = self.viewsById[boundId]
                except KeyError:
                    raise LookupError(f"View {boundId!r} not found!") from None
        
        tokenKey = (view.id, clientId)
        token = self._mintViewToken()
        self.tokens[tokenKey] = token
        
        tracer.traceEvent(
            "viewRegistry.getOrCreateViewForClient",
            attrs={
                "clientId": clientId,
                "viewId": view.id,
                "viewKind": view.viewKind,
                "createdNew": createdNew,
            },
            level="info",
            tags=["viewRegistry"],
        )
        
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
        
        tracer = getTracer()
        tracer.traceEvent(
            "viewRegistry.issueToken",
            attrs={
                "viewId": viewId,
                "clientId": clientId,
            },
            level="debug",
            tags=["viewRegistry"],
        )
        
        return token
    
    def revokeToken(self, viewId: str, clientId: str) -> None:
        """Removes the token for (viewId, clientId) if present."""
        removed = self.tokens.pop((viewId, clientId), None) is not None
        
        tracer = getTracer()
        tracer.traceEvent(
            "viewRegistry.revokeToken",
            attrs={
                "viewId": viewId,
                "clientId": clientId,
                "hadToken": removed,
            },
            level="debug",
            tags=["viewRegistry"],
        )

    def validateToken(self, viewId: str, clientId: str, token: str) -> bool:
        if not viewId or not clientId or not token:
            return False
        
        key = (viewId, clientId)
        stored = self.tokens.get(key)
        # Timing-safe comparison
        ok = stored is not None and secrets.compare_digest(stored, token)

        tracer = getTracer()
        tracer.traceEvent(
            "viewRegistry.validateToken",
            attrs={
                "viewId": viewId,
                "clientId": clientId,
                "result": ok,
                "hasStored": stored is not None,
            },
            level="debug",
            tags=["viewRegistry"],
        )
        
        return ok
    
    def getViewById(self, viewId: str) -> View | None:
        if not isinstance(viewId, str) or not viewId.strip():
            raise ValueError(f"'viewId' must be non-empty string; got {repr(viewId)}")
        
        return self.viewsById.get(viewId)
    
    def bindClientToView(self, clientId: str, viewId: str, viewKind: str | None = None) -> None:
        if not isinstance(viewId, str) or not viewId.strip():
            raise ValueError(f"'viewId' must be non-empty string; got {repr(viewId)}")
        if not isinstance(clientId, str) or not clientId.strip():
            raise ValueError(f"'clientId' must be non-empty string; got {repr(clientId)}")

        if viewKind is None:
            # Derive from existing view if present. Fallback to "main"
            existing = self.viewsById.get(viewId)
            viewKind = getattr(existing, "viewKind", "main") if existing else "main"
        viewKind = (viewKind or "main").strip() or "main"
        
        key = (clientId, viewKind)
        oldViewId = self.bindingsByClientAndViewKindKey.get(key)
        if oldViewId and oldViewId != viewId:
            self.tokens.pop((oldViewId, clientId), None)
        
        self.bindingsByClientAndViewKindKey[key] = viewId
        if viewId not in self.viewsById:
            self.viewsById[viewId] = View(viewId=viewId, viewKind=viewKind)
        
        tracer = getTracer()
        tracer.traceEvent(
            "viewRegistry.bindClientToView",
            attrs={
                "clientId": clientId,
                "viewId": viewId,
                "viewKind": viewKind,
                "previousViewId": oldViewId,
            },
            level="info",
            tags=["viewRegistry"],
        )
    
    def unbindClient(self, clientId: str) -> bool:
        """
        Removes all bindings for this clientId and any tokens associated with them.
        """
        removedAny = False
        removedTokens = 0
        removedViewIds: set[str] = set()
        
        for (cid, viewKind), viewId in list(self.bindingsByClientAndViewKindKey.items()):
            if cid != clientId:
                continue
            self.bindingsByClientAndViewKindKey.pop((cid, viewKind), None)
            removedAny = True
            removedViewIds.add(viewId)
            if self.tokens.pop((viewId, clientId), None) is not None:
                removedTokens += 1
        
        tracer = getTracer()
        tracer.traceEvent(
            "viewRegistry.unbindClient",
            attrs={
                "clientId": clientId,
                "removedViewIds": sorted(removedViewIds),
                "hadBindings": removedAny,
                "hadToken": bool(removedTokens),
                "removedTokenCount": removedTokens,
            },
            level="info",
            tags=["viewRegistry"],
        )
        
        return removedAny

    def destroyView(self, viewId: str) -> bool:
        view = self.viewsById.pop(viewId, None)
        tracer = getTracer()
        if not view:
            tracer.traceEvent(
                "viewRegistry.destroyView",
                attrs={
                    "viewId": viewId,
                    "existed": False,
                    "unboundClients": 0,
                },
                level="debug",
                tags=["viewRegistry"],
            )
            return False
        
        # Close the view lifecycle span
        try:
            view.destroy()
        except Exception:
            # Never let a broken view destroy() kill registry.
            pass
        
        # Unbind all clients pointing to this view (with or without a token)
        unboundClients = 0
        for (cid, viewKind), vid in list(self.bindingsByClientAndViewKindKey.items()):
            if vid != viewId:
                continue
            self.bindingsByClientAndViewKindKey.pop((cid, viewKind), None)
            self.tokens.pop((viewId, cid), None)
            unboundClients += 1
        
        tracer.traceEvent(
            "viewRegistry.destroyView",
            attrs={
                "viewId": viewId,
                "existed": True,
                "unboundClients": unboundClients,
            },
            level="info",
            tags=["viewRegistry"],
        )
        
        return True

viewRegistry = ViewRegistry()
