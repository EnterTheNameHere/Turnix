# backend/api/bootstrap.py
from __future__ import annotations
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.app.config import config, configBool
from backend.views.registry import viewRegistry
from backend.app import state

logger = logging.getLogger(__name__)
router = APIRouter()



@router.post("/api/bootstrap")
async def apiBootstrap(request: Request):
    # Ensure/assign clientId via cookie
    reqCookies = request.cookies or {}
    clientId = viewRegistry.ensureClientId(reqCookies)

    view, token = viewRegistry.getOrCreateViewForClient(clientId)
    
    # Ensure the view has access to at least the shell session while in menu.
    shell = state.APP_SHELL
    if shell and not view.isAttached(shell.shellSession.id):
        view.attachSession(shell.shellSession.id)
    
    payload = {
        "viewId": view.id,
        "viewToken": token,
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
    
    logger.info(
        "[BOOTSTRAP] clientId='%s' viewId='%s' hasCookie='%s' -> token='%s'",
        clientId,
        view.id,
        "clientId" in reqCookies,
        token[:8] + "…" # shortened for readability
    )
    logger.debug("[BOOTSTRAP] Payload: %s", payload)

    return resp
