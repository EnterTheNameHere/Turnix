# backend/app/factory.py
from __future__ import annotations
import logging
from collections.abc import Sequence

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.lifecycle import life
from backend.app.static_mount import mountStatic
from backend.kernel import Kernel
from backend.rpc.transport import mountWebSocket
from backend.runtimes.base import RuntimeInstance
from backend.runtimes.main_menu_runtime import MainMenuRuntime



def createApp(*, extraRouters: Sequence[APIRouter] = (), initialRuntime: RuntimeInstance | None = None) -> FastAPI:
    # Early, process-wide bootstrap (idempotent)
    from backend.app.config import initConfig
    initConfig()
    from backend.core.logger import configureLogging
    configureLogging()
    from backend.core.permissions import initPermissions
    initPermissions()
    
    logger = logging.getLogger(__name__)
    
    # Find how many unresolved references we have in config schemas
    from backend.app.globals import getConfigService
    unresolved = getConfigService().registry.findUnresolvedRefs()
    if unresolved:
        logger.warning("Config: unresolved $ref ids: %s", ", ".join(unresolved))
    
    # Turnix Boss. It registers itself to globals.
    kernel = Kernel()
    
    # Create main menu runtime
    configService = getConfigService()
    if initialRuntime is None:
        initialRuntime = MainMenuRuntime(
            configService=configService,
            configRegistry=configService.registry,
            globalConfigView=configService.globalStore,
        )
    kernel.switchRuntime(initialRuntime)
    
    app = FastAPI(lifespan=life)
    
    from backend.app.globals import config
    # ----- CORS (cookies-ready) -----
    corsOrigins = config("http.cors.allowOrigins", ["http://localhost:5173", "http://127.0.0.1:5173"])
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
