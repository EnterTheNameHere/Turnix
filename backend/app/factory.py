# backend/app/factory.py
from __future__ import annotations
import logging
from collections.abc import Sequence

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.settings import settings
from backend.app.lifecycle import life
from backend.app.static_mount import mountStatic
from backend.core.logger import configureLogging
from backend.rpc.transport import mountWebSocket



def createApp(*, extraRouters: Sequence[APIRouter] = ()) -> FastAPI:
    # Ensure logging is configured ONCE
    configureLogging()
    logger = logging.getLogger(__name__)

    app = FastAPI(lifespan=life)

    # ----- CORS (cookies-ready) -----
    corsOrigins = settings("http.cors.allowOrigins", ["http://localhost:5173", "http://127.0.0.1:5173"])
    if not isinstance(corsOrigins, list):
        corsOrigins = []

    app.add_middleware(
        CORSMiddleware,
        allow_origins=corsOrigins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ----- Routers first -----
    from backend.app.web import router as webRouter
    from backend.api.bootstrap import router as bootstrapRouter
    from backend.mods.frontend_index import router as frontendRouter

    app.include_router(webRouter)
    app.include_router(bootstrapRouter)
    app.include_router(frontendRouter)

    for router in extraRouters:
        app.include_router(router)

    # ----- WebSocket endpoint -----
    mountWebSocket(app)

    # ----- Static comes last so it doesn't shadow routes -----
    mountStatic(app)

    logger.info("Backend initialized with %d extra routers(s)", len(extraRouters))
    return app
