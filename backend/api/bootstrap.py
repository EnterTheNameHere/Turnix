# backend/api/bootstrap.py
from __future__ import annotations
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.app.globals import getActiveRuntime, getActiveAppPack, config, configBool, getTracer
from backend.content.runtime_bootstrap import buildViewContextForRuntime
from backend.mods.roots_registry import registerRoots
from backend.views.registry import viewRegistry

logger = logging.getLogger(__name__)
router = APIRouter()



@router.post("/api/bootstrap")
async def apiBootstrap(request: Request):
    # Ensure/assign clientId via cookie
    reqCookies = request.cookies or {}
    clientId = viewRegistry.ensureClientId(reqCookies)

    # Read viewKind from JSON body (frontend sends it)
    try:
        body = await request.json()
    except Exception:
        body = {}
    viewKind = (body or {}).get("viewKind") or "main"
    viewKind = str(viewKind).strip() or "main"
    
    # Let the registry decide which view to attach for this client + kind
    view, token = viewRegistry.getOrCreateViewForClient(clientId, viewKind=viewKind)
    
    # Ensure the view has access to at least the shell session while in menu.
    runtime = getActiveRuntime()
    if runtime and runtime.mainSession and not view.isAttached(runtime.mainSession.id):
        view.attachSession(runtime.mainSession.id)
    
    # Build ViewContext for this view (if we have an active appPack)
    appPack = getActiveAppPack()
    if runtime and appPack:
        try:
            viewContext = buildViewContextForRuntime(
                runtimeInstance=runtime,
                appPack=appPack,
                viewKind=viewKind,
            )
            # Register extra mod roots for this viewId so scanModsForMount(viewId, …)
            # can pick them up when scanning mods for frontend/backend.
            if viewContext.extraModRoots:
                registerRoots(view.id, viewContext.extraModRoots)
        except Exception:
            # Failure to resolve viewPack or register roots must not break bootstrap...
            logger.exception("Failed to build/register ViewContext for view '%s'", view.id)
    payload = {
        "viewId": view.id,
        "viewToken": token,
        "viewKind": viewKind,
        "serverGen": view.version, # Use View.version as a single gen id for now
    }

    resp = JSONResponse(payload)

    # Decide cookie flags
    scheme = request.url.scheme
    cookieArrivedBySecure = (scheme == "https")
    cookieSecure = configBool("http.cookie.secure", cookieArrivedBySecure)
    cookieSameSite = str(config("http.cookie.sameSite", "lax")).lower()
    if cookieSameSite not in ("lax", "strict", "none"):
        cookieSameSite = "lax"
    cookieMaxAge = int(config("http.cookie.maxAgeSec", 60*60*24*30)) # 30 days default

    # Set HttpOnly cookie if missing / rotate to keep it fresh
    if reqCookies.get("clientId") != clientId:
        resp.set_cookie(
            key="clientId",
            value=clientId,
            httponly=True,
            samesite=cookieSameSite,
            secure=cookieSecure,
            path="/",
            max_age=cookieMaxAge,
        )
    
    hasCookie = "clientId" in reqCookies
    
    # Trace bootstrap boundary so the viewer can see who got which view.
    tracer = getTracer()
    try:
        tracer.traceEvent(
            "http.bootstrap",
            level="info",
            tags=["http", "bootstrap"],
            attrs={
                "clientId": clientId,
                "viewId": view.id,
                "viewKind": viewKind,
                "hasCookie": hasCookie,
                "scheme": scheme,
                "cookieSameSite": cookieSameSite,
                "cookieSecure": cookieSecure,
                "cookieMaxAgeSec": cookieMaxAge,
                "tokenPreview": token[:8] + "…",
            },
        )
    except Exception:
        # Tracing must not break bootstrap.
        pass
    
    logger.debug("[BOOTSTRAP] Payload: %s", payload)

    return resp
